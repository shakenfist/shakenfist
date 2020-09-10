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

    handler = logging_handlers.SysLogHandler(address='/dev/log')
    handler.setFormatter(TextFormatter(
        fmt='%(levelname)s %(message)s', colorize=False))
    log.addHandler(handler)

    return log, handler


LOG, _ = setup('main')
METHOD_RE = re.compile('.* in (.*)')


def _log(level, relatedobjects, message):
    # Determine the name of the calling method
    f = traceback.format_stack()[-3]
    fm = METHOD_RE.match(f)
    if fm:
        f = fm.group(1)

    # Build a structured log line
    log_ctx = LOG.withPrefix(
        '%s[%s]' % (setproctitle.getproctitle(), os.getpid()))

    fields = {'method': f}
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
