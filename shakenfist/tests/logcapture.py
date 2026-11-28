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

The host's syslog socket is redirected on the same terms, and for a
different reason worth stating separately (issue #5106). ``logs.setup()``
installs a ``SysLogHandler('/dev/log')`` whenever
``SHAKENFIST_LOG_TO_STDOUT`` is not ``'1'``, which is every way of running
the suite that does not inherit ``tox.ini``'s setenv: an IDE test
explorer, a hand-typed ``stestr run`` in an activated venv, an agent
driving the venv interpreter. That variable describes how the suite was
invoked rather than the fact that a test is running, and the two times
this was caught before (#4725, and ``testenv:cover`` not inheriting
``{[testenv]setenv}``) were both fixed at the invocation site, which the
third path does not have -- its caller is a person or a tool, not a file
in this repository. So the decision is taken here instead, where being
inside a test is the whole premise. Left alone, a run outside tox wrote
every record to the host's journal: 97-100% of one workstation's syslog
volume, 14.8 GB in a week.

A syslog handler cannot be redirected the way a console one is -- it owns
a socket, not a stream, and has no ``setStream()`` -- so it is lifted off
its logger for the duration of the test and a ``StreamHandler`` on the
capture buffer is put in its place, carrying the original's formatter,
level and filters so what a failing test reports is what it would have
logged.

The boundary, measured rather than reasoned about. Running
``shakenfist.tests.test_daemon_{cleaner,loglevel}`` (101 tests) straight
off the venv interpreter, with no tox and so no setenv, wrote 247 records
to the host's journal before these changes and none after. Forcing
``SHAKENFIST_LOG_TO_STDOUT=0`` -- which ``shakenfist/tests/__init__.py``
leaves alone on purpose, for anyone deliberately exercising the syslog
branch -- leaks 17 of them, all from ``logs.setup()`` running at *import*
time and the module logging before any test started. Per-test capture
cannot cover import time by construction: there is no test yet. The
default in ``shakenfist/tests/__init__.py`` is what covers it, by making
the handler a stdout one in the first place, which is the other half of
why both changes exist.
"""

import io
import logging
import sys
from logging import handlers as logging_handlers
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


def _writes_to_host_syslog(handler):
    """Is this handler writing to a syslog socket on this machine?

    Discriminates on the address rather than the class. A tuple address
    is a network sink, which is not this host's syslog and not what this
    module exists to contain; a string address is a unix socket on this
    machine, which is. Taking away a handler a test built for itself
    would be overreach, and would make ``duplicate_handlers()`` report it
    as an escape when it is nothing of the kind. Same distinction
    ``_writes_to_console()`` draws between the process's real streams and
    a stream a test substituted.

    ``test_logship.py`` is the existing test which builds its own
    ``SysLogHandler``, at ``('localhost', 0)`` with the socket mocked, to
    check that logship leaves the per-module handler in place. Note that
    it is unaffected either way today: its ``setUp()`` calls
    ``super().setUp()`` first, so the handler is attached after capture
    has already started and was never a candidate. The discrimination is
    what keeps that true if such a handler ever moves to module scope or
    import time, which is where ``logs.setup()`` installs the real one.
    """
    if not isinstance(handler, logging_handlers.SysLogHandler):
        return False
    # ``unixsocket`` is what the handler itself concluded: createSocket()
    # sets it True for a str address and False for a tuple, and __init__
    # always calls createSocket(), so it is set by the time any handler
    # reaches us. Read that rather than re-deriving it from .address,
    # which would restate a stdlib decision instead of using it.
    return bool(getattr(handler, 'unixsocket', False))


def _handlers_matching(predicate):
    """Every (logger, handler) pair whose handler satisfies ``predicate``.

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
            if predicate(handler):
                found.append((logger, handler))
    return found


def console_handlers():
    """Every StreamHandler currently writing to the real stdout or stderr."""
    return _handlers_matching(
        lambda handler: (isinstance(handler, logging.StreamHandler)
                         and _writes_to_console(handler)))


def syslog_handlers():
    """Every SysLogHandler currently bound to this host's syslog socket."""
    return _handlers_matching(_writes_to_host_syslog)


def escaping_handlers():
    """Every handler currently writing outside the test."""
    return console_handlers() + syslog_handlers()


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
    """Contain the log handlers which write outside the test, for one test.

    Console handlers are pointed at an in-memory buffer; host-syslog
    handlers are lifted off their loggers and a buffer handler stands in.
    """

    def __init__(self):
        self.buffer = io.StringIO()
        self._restore = []
        self._substituted = []
        self._redirected = set()

    def start(self):
        for logger, handler in console_handlers():
            self._redirected.add(id(logger))
            self._restore.append((handler, handler.stream))
            handler.setStream(self.buffer)

        # A syslog handler owns a socket rather than a stream, so there is
        # no setStream() to point at the buffer. Lift it off its logger for
        # the test and stand a StreamHandler in for it, so the records are
        # still captured for a failing test rather than merely silenced.
        for logger, handler in syslog_handlers():
            self._redirected.add(id(logger))
            stand_in = logging.StreamHandler(self.buffer)
            # Carry the formatter, level and filters across: what a failing
            # test reports should be what it would have written to syslog,
            # and what the handler would have dropped is part of that.
            stand_in.setFormatter(handler.formatter)
            stand_in.setLevel(handler.level)
            for record_filter in handler.filters:
                stand_in.addFilter(record_filter)
            logger.removeHandler(handler)
            logger.addHandler(stand_in)
            self._substituted.append((logger, handler, stand_in))
        return self

    def duplicate_handlers(self):
        """Loggers given a second escaping handler during this test.

        Capture redirects every handler which existed when the test
        started -- console handlers by pointing their stream at the
        buffer, host-syslog handlers by standing a buffer handler in for
        them -- so anything still writing to the real stdout, stderr or
        ``/dev/log`` was installed while the test ran. On a logger which
        had none that is ordinary: a module imported for the first time
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
                       for logger, handler in escaping_handlers()
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

        # Undone in reverse, and the stand-in is removed before the real
        # handler goes back, so a logger never briefly carries both. The
        # real handler returns at the end of the logger's handler list
        # rather than at its old position; order only sequences emits.
        while self._substituted:
            logger, handler, stand_in = self._substituted.pop()
            # Only if the stand-in is still there. Something took it off
            # during the test otherwise -- forget_logger() in a cleanup,
            # which runs before this, or code under test clearing the
            # logger -- and putting the real handler back would undo that,
            # leaving a live syslog handler on a logger the test cleared.
            # Same effect as setStream() on a detached console handler,
            # which is inert.
            if stand_in in logger.handlers:
                logger.removeHandler(stand_in)
                logger.addHandler(handler)
            stand_in.close()

        captured = self.buffer.getvalue() if keep else None
        self.buffer.close()
        self.buffer = io.StringIO()
        return captured
