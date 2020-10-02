import copy
import logging
from logging import handlers as logging_handlers
import os
from pylogrus import TextFormatter
from pylogrus.base import PyLogrusBase
import re
import setproctitle
import traceback


# These classes are extensions of the work in https://github.com/vmig/pylogrus
class SFPyLogrus(logging.Logger, PyLogrusBase):

    def __init__(self, *args, **kwargs):
        extra = kwargs.pop('extra', None)
        self._extra_fields = extra or {}
        super(SFPyLogrus, self).__init__(*args, **kwargs)

    def withPrefix(self, prefix=None):
        return SFCustomAdapter(self, None, prefix)

    def withFields(self, fields=None):
        return SFCustomAdapter(self, fields)

    def withInstance(self, inst):
        if not isinstance(inst, str):
            inst = inst.db_entry['uuid']
        return SFCustomAdapter(self, {'instance': inst})


class SFCustomAdapter(logging.LoggerAdapter, PyLogrusBase):

    def __init__(self, logger, extra=None, prefix=None):
        """Logger modifier.

        :param logger: Logger instance
        :type logger: PyLogrus
        :param extra: Custom fields
        :type extra: dict | None
        :param prefix: Prefix of log message
        :type prefix: str | None
        """
        self._logger = logger
        self._extra = self._normalize(extra)
        self._prefix = prefix
        super(SFCustomAdapter, self).__init__(
            self._logger, {'extra_fields': self._extra, 'prefix': self._prefix})

    @staticmethod
    def _normalize(fields):
        return {k.lower(): v for k, v in fields.items()} if isinstance(fields, dict) else {}

    def withFields(self, fields=None):
        extra = copy.deepcopy(self._extra)
        extra.update(self._normalize(fields))
        return SFCustomAdapter(self._logger, extra, self._prefix)

    def withPrefix(self, prefix=None):
        return self if prefix is None else SFCustomAdapter(self._logger, self._extra, prefix)

    def process(self, msg, kwargs):
        kwargs["extra"] = self.extra
        msg = '%s[%s] %s' % (setproctitle.getproctitle(), os.getpid(), msg)
        return msg, kwargs

    def withInstance(self, inst):
        extra = copy.deepcopy(self._extra)
        if not isinstance(inst, str):
            inst = inst.db_entry['uuid']
        extra.update({'instance': inst})
        return SFCustomAdapter(self._logger, extra, self._prefix)


def setup(name):
    logging.setLoggerClass(SFPyLogrus)

    # Set root log level - higher handlers can set their own filter level
    logging.root.setLevel(logging.DEBUG)
    log = logging.getLogger(name)

    if log.hasHandlers():
        handler = log.handlers[0]
    else:
        # Add our handler
        handler = logging_handlers.SysLogHandler(address='/dev/log')
        handler.setFormatter(TextFormatter(
            fmt='%(levelname)s %(message)s', colorize=False))
        log.addHandler(handler)

    return log.withPrefix(), handler


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
        '**OLD** %s[%s]' % (setproctitle.getproctitle(), os.getpid()))

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
