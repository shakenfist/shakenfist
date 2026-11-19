# Copyright 2026 Michael Still and contributors

"""Keep a test's log records out of the stestr worker's result stream.

``tox.ini`` sets ``SHAKENFIST_LOG_TO_STDOUT=1``, which makes
``shakenfist_utilities.logs.setup()`` install a
``logging.StreamHandler(sys.stdout)``. That runs once per module, at
import time, so the handler holds the process's real stdout -- which in
a stestr worker is also the subunit result stream. Every record a test
emitted was therefore written into that stream, forwarded by the stestr
parent to its console, and from there through tox into the CI log: 82 MB
for one run, and a test which logged heavily ran no faster than the
console could be drained. One scheduler test took between 37 and 165
seconds on CI for 1.2 seconds of work (issue #4991).

Nothing replaced the stream per test either, so the "stestr captures
the logging and shows it only for a failing test" that
``shakenfist/tests/__init__.py`` has always described was not happening.
``ShakenFistTestCase`` now points those handlers at an in-memory buffer
for the duration of each test and restores them afterwards, and attaches
what was captured as a ``log`` detail only when the test did not pass --
details are transmitted on success as well, so attaching it
unconditionally would send the whole 82 MB back over the result stream.

Stderr is redirected on the same terms. ``shakenfist/client/ctl.py``
calls ``logging.basicConfig()`` at import time, which puts a stderr
handler on the *root* logger for the rest of the worker's life, and the
suite emitted 33,000 lines that way -- a tenth of the lines in the run's
log, from one import.

A handler installed *during* a test -- by a module imported for the
first time inside it -- binds the real stream and escapes the
redirection until the next test starts, which is why
``duplicate_handlers()`` reports the case that matters rather than every
case. ``sys.stdout`` and ``sys.stderr`` themselves are deliberately not
replaced: in a worker stdout is the subunit stream, which subunit writes
its own packets to.
"""

import io
import logging
import sys
from unittest.case import SkipTest


# The process's real stdout and stderr, as they were before any test
# ran. Recorded here rather than read at capture time because a test may
# legitimately replace sys.stdout, and a handler bound to a test's own
# replacement is that test's business, not ours.
_REAL_STDOUT = sys.stdout
_REAL_STDERR = sys.stderr


def _writes_to_console(handler):
    """Is this handler writing to the process's real stdout or stderr?"""
    stream = getattr(handler, 'stream', None)
    if stream is None:
        return False
    # Identity, not equality: a replacement stream may be anything,
    # including an object with an __eq__ of its own.
    return any(stream is console for console in
               (_REAL_STDOUT, _REAL_STDERR, sys.__stdout__, sys.__stderr__))


def console_handlers():
    """Every StreamHandler currently writing to the real stdout or stderr.

    Walks every logger, because ``logs.setup()`` installs its handler on
    the calling module's own logger rather than on the root.
    """
    found = []
    # Snapshots, because a thread left running by an earlier test can
    # import a module -- and so create a logger -- while this walks.
    loggers = [logging.getLogger()]
    loggers.extend(logger for logger in list(logging.Logger.manager.loggerDict.values())
                   if isinstance(logger, logging.Logger))

    for logger in loggers:
        for handler in list(logger.handlers):
            if isinstance(handler, logging.StreamHandler) and _writes_to_console(handler):
                found.append((logger, handler))
    return found


def note_failure(flag, exc_info):
    """Record, in ``flag``, that a test did not pass.

    Called from a ``addOnException()`` handler so the capture knows
    whether to report what it caught. It is a module-level function
    reached through a closure over ``flag``, rather than a method,
    because testtools keeps a test's exception handlers for the life of
    the test object: a bound method there puts the instance in a
    reference cycle, so everything the test allocated survives until
    the cycle collector next runs -- which is exactly what the per-test
    memory bound in ``shakenfist/tests/memory.py`` reads as the *next*
    test's growth.

    A skip is not a failure and its log is noise, so it is ignored.
    """
    if exc_info[0] is not None and issubclass(exc_info[0], SkipTest):
        return
    flag.append(True)


def forget_logger(name):
    """Drop every handler from a named logger.

    For the test which constructs something that calls ``logs.setup()``
    -- a ``Daemon``, for instance. That installs a handler on a
    process-global logger, mid test, after capture started, and nothing
    removes it: the handler then writes into the worker's result stream
    for the rest of the run. Register this as a cleanup.
    """
    logger = logging.getLogger(name)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)


class LogCapture:
    """Redirect the console log handlers into memory for one test."""

    def __init__(self):
        self.buffer = io.StringIO()
        self._restore = []
        self._redirected = set()

    def start(self):
        for logger, handler in console_handlers():
            self._redirected.add(id(logger))
            self._restore.append((handler, handler.stream))
            handler.setStream(self.buffer)
        return self

    def duplicate_handlers(self):
        """Loggers given a second console handler during this test.

        Capture redirects every console handler which existed when the
        test started, so anything still writing to the real stdout or
        stderr was installed while the test ran. On a logger which had
        none that is ordinary: a module imported for the first time
        inside a test calls ``logs.setup()``, which is how that logger
        gets its one handler, and the next test captures it.

        On a logger which already had one it is the defect this module
        exists for. Nothing removes a handler added in a ``setUp()``, so
        the next test in the worker inherits it and adds another, and a
        logger ends up with one handler per test which has run: 45 lines
        of ``app.logger.addHandler(logging.StreamHandler(sys.stdout))``
        across the API tests had 104 tests in ``test_auth.py`` emitting
        8,939 copies of a few hundred records.
        """
        return sorted({'%s (%s)' % (logger.name or 'root',
                                    type(handler).__name__)
                       for logger, handler in console_handlers()
                       if id(logger) in self._redirected})

    def stop(self, keep=False):
        """Restore the handlers' streams, returning the captured text.

        ``keep`` False discards the buffer without materialising it: a
        passing test's log is not wanted, and a test which logged
        megabytes would otherwise be charged for a second copy of it by
        the per-test memory bound in ``shakenfist/tests/memory.py``.
        """
        while self._restore:
            handler, stream = self._restore.pop()
            handler.setStream(stream)

        captured = self.buffer.getvalue() if keep else None
        self.buffer.close()
        self.buffer = io.StringIO()
        return captured
