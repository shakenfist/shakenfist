"""Per-test capture of log handlers which write to stdout or stderr.

With ``SHAKENFIST_LOG_TO_STDOUT=1`` (set in tox.ini),
``shakenfist_utilities.logs.setup()`` gives each module's logger a
``StreamHandler(sys.stdout)``. That runs at import, so the handler holds
the process's real stdout -- which, under stestr, is the worker's subunit
result stream. Nothing replaced it per test, so every record every test
emitted went raw into that stream, and from there through the stestr
parent, tox and the CI runner's log. That chain drains slowly enough that
a test which logs heavily ran at the speed of the drain: one scheduler
test wrote 13 MB and took 165 seconds on CI against 1.5 locally, and the
unit test step timed out with every test passing (issue #4991).

The same goes for stderr: a module which calls ``logging.basicConfig()``
(``client/ctl.py``, the CI suite's base) gives the root logger a
``StreamHandler(sys.stderr)`` when a test imports it, and every shakenfist
record propagates there too.

``capture()`` points each such handler at an in-memory buffer for the
length of one test, and restores it afterwards. The buffer is attached to
the result as a ``log`` detail only when the test fails or errors, which
is what stestr's "only shown for failing tests" was always assumed to do.
"""
import io
import logging
import sys
import weakref

from testtools import content


def _loggers():
    yield logging.getLogger()
    for logger in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger):
            yield logger


def _standard_stream_handlers():
    """Every stream handler whose stream is the process's stdout or stderr.

    A handler bound to a file, or to a stream a test installed itself, is
    left alone: only the import-time binding to a standard stream is the
    problem.
    """
    streams = {id(sys.stdout), id(sys.__stdout__),
               id(sys.stderr), id(sys.__stderr__)}
    seen = set()
    for logger in _loggers():
        for handler in logger.handlers:
            if id(handler) in seen:
                continue
            seen.add(id(handler))
            if (isinstance(handler, logging.StreamHandler)
                    and not isinstance(handler, logging.FileHandler)
                    and id(handler.stream) in streams):
                yield handler


def capture(case):
    """Capture standard stream logging for the duration of ``case``.

    Call from ``setUp()``. Returns the buffer, mostly so that tests of
    this module can read it.

    A logger first created part way through the test -- a module imported
    lazily -- is not captured until the next test, so its first records
    still reach the pipe. That is a few lines per module per worker.
    """
    buffer = io.StringIO()
    for handler in _standard_stream_handlers():
        original = handler.setStream(buffer)
        case.addCleanup(handler.setStream, original)

    # The handler is stored on the case, so it must not hold the case
    # strongly: that cycle would leave every test instance, and whatever
    # it allocated, for the cycle collector rather than freeing it when
    # the test is dropped -- and ShakenFistTestCase's memory guard would
    # then charge one test's allocation to whichever test ran next.
    case_ref = weakref.ref(case)
    skip = case.skipException

    def attach(exc_info):
        if exc_info and issubclass(exc_info[0], skip):
            return
        failed = case_ref()
        if failed is None:
            return
        # Read lazily: the detail is serialised when the result is
        # reported, after the cleanups, so records logged by a failing
        # cleanup are included too.
        failed.addDetail('log', content.Content(
            content.UTF8_TEXT, lambda: [buffer.getvalue().encode('utf-8')]))

    case.addOnException(attach)
    return buffer
