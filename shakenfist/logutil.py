import logging
from logging import handlers as logging_handlers
import os
from pylogrus import PyLogrus
from pylogrus import TextFormatter
import re
import setproctitle
import traceback


def setup(name):
    logging.setLoggerClass(PyLogrus)
    log = logging.getLogger(__name__)

    # Remove old / default handlers
    while log.hasHandlers():
        log.removeHandler(log.handlers[0])

    # Add our handler
    handler = logging_handlers.SysLogHandler(address='/dev/log')
    handler.setFormatter(TextFormatter(
        fmt='%(levelname)s %(message)s', colorize=False))
    log.addHandler(handler)

    return log, handler


LOG, _ = setup('main')
FILENAME_RE = re.compile('.*/dist-packages/shakenfist/(.*)')


def _log(level, relatedobjects, message):
    # Determine the name of the calling method
    filename = traceback.extract_stack()[-3].filename
    fmatch = FILENAME_RE.match(filename)
    if fmatch:
        filename = fmatch.group(1)
    caller = '%s:%s:%s()' % (filename,
                             traceback.extract_stack()[-3].lineno,
                             traceback.extract_stack()[-3].name)

    # Build a structured log line
    log_ctx = LOG.withPrefix(
        '%s[%s]' % (setproctitle.getproctitle(), os.getpid()))

    fields = {'method': caller}
    generic_counter = 1

    if relatedobjects:
        for obj in relatedobjects:
            try:
                n, v = obj.get_describing_tuple()
                fields[n] = v
            except Exception:
                fields['generic-%s' % generic_counter] = str(obj)
                generic_counter += 1

    # Actually log
    log_ctx.withFields(fields).__getattribute__(level)(message)


def debug(relatedobjects, message):
    _log('debug', relatedobjects, message)


def info(relatedobjects, message):
    _log('info', relatedobjects, message)


def warning(relatedobjects, message):
    _log('warning', relatedobjects, message)


def error(relatedobjects, message):
    _log('error', relatedobjects, message)


def fatal(relatedobjects, message):
    _log('fatal', relatedobjects, message)
