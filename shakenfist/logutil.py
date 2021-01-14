import copy
import logging
from logging import handlers as logging_handlers
import os
from pylogrus import TextFormatter
from pylogrus.base import PyLogrusBase
import re
import setproctitle
import traceback

from shakenfist.config import config


# These classes are extensions of the work in https://github.com/vmig/pylogrus
class SFPyLogrus(logging.Logger, PyLogrusBase):

    def __init__(self, *args, **kwargs):
        extra = kwargs.pop('extra', None)
        self._extra_fields = extra or {}
        super(SFPyLogrus, self).__init__(*args, **kwargs)

    def withPrefix(self, prefix=None):
        return self.with_prefix(prefix)

    def withFields(self, fields=None):
        return self.with_fields(fields)

    def with_prefix(self, prefix=None):
        return SFCustomAdapter(self, None, prefix)

    def with_fields(self, fields=None):
        return SFCustomAdapter(self, fields)

    def with_field(self, key, value):
        return SFCustomAdapter(self, {key: value})

    #
    # Convenience methods
    #
    def with_object(self, obj):
        if not obj:
            return SFCustomAdapter(self, {})
        try:
            label, value = obj.unique_label()
        except Exception as e:
            raise Exception('Bad object - no unique_label() function: %s' % e)
        return SFCustomAdapter(self, {label: value})

    #
    # Use labelled convenience methods when ID is a string (not object)
    # Note: the helper method still handles objects
    #
    def with_instance(self, inst):
        if not isinstance(inst, str):
            inst = inst.uuid
        return SFCustomAdapter(self, {'instance': inst})

    def with_network(self, inst):
        if not isinstance(inst, str):
            inst = inst.uuid
        return SFCustomAdapter(self, {'network': inst})

    def with_networkinterface(self, inst):
        if not isinstance(inst, str):
            inst = inst.uuid
        return SFCustomAdapter(self, {'networkinterface': inst})

    def with_image(self, inst):
        if not isinstance(inst, str):
            inst = inst.unique_ref
        return SFCustomAdapter(self, {'image': inst})


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

    def withPrefix(self, prefix=None):
        return self.with_prefix(prefix)

    def withFields(self, fields=None):
        return self.with_fields(fields)

    def with_fields(self, fields=None):
        extra = copy.deepcopy(self._extra)
        extra.update(self._normalize(fields))
        return SFCustomAdapter(self._logger, extra, self._prefix)

    def with_field(self, key, value):
        extra = copy.deepcopy(self._extra)
        extra.update(self._normalize({key: value}))
        return SFCustomAdapter(self._logger, extra, self._prefix)

    def with_prefix(self, prefix=None):
        return self if prefix is None else SFCustomAdapter(self._logger, self._extra, prefix)

    FILENAME_RE = re.compile('.*/dist-packages/shakenfist/(.*)')

    def process(self, msg, kwargs):
        msg = '%s[%s] %s' % (setproctitle.getproctitle(), os.getpid(), msg)
        kwargs["extra"] = self.extra

        if config.get('LOG_METHOD_TRACE'):
            # Determine the name of the calling method
            filename = traceback.extract_stack()[-4].filename
            f_match = self.FILENAME_RE.match(filename)
            if f_match:
                filename = f_match.group(1)
            caller = '%s:%s:%s()' % (filename,
                                     traceback.extract_stack()[-4].lineno,
                                     traceback.extract_stack()[-4].name)
            self._extra['method'] = caller

        return msg, kwargs

    #
    # Convenience methods
    #
    def with_object(self, obj):
        extra = copy.deepcopy(self._extra)
        if obj:
            try:
                label, value = obj.unique_label()
            except Exception as e:
                raise Exception(
                    'Bad object - no unique_label() function: %s' % e)
            extra.update({label: value})
        return SFCustomAdapter(self._logger, extra, self._prefix)

    #
    # Use labelled convenience methods when ID is a string (not object)
    # Note: the helper method still handles objects
    #
    def with_instance(self, inst):
        extra = copy.deepcopy(self._extra)
        if not isinstance(inst, str):
            inst = inst.uuid
        extra.update({'instance': inst})
        return SFCustomAdapter(self._logger, extra, self._prefix)

    def with_network(self, inst):
        extra = copy.deepcopy(self._extra)
        if not isinstance(inst, str):
            inst = inst.uuid
        extra.update({'network': inst})
        return SFCustomAdapter(self._logger, extra, self._prefix)

    def with_networkinterface(self, inst):
        extra = copy.deepcopy(self._extra)
        if not isinstance(inst, str):
            inst = inst.uuid
        extra.update({'networkinterface': inst})
        return SFCustomAdapter(self._logger, extra, self._prefix)

    def with_image(self, inst):
        extra = copy.deepcopy(self._extra)
        if not isinstance(inst, str):
            inst = inst.unique_ref
        extra.update({'image': inst})
        return SFCustomAdapter(self._logger, extra, self._prefix)


def setup(name):
    logging.setLoggerClass(SFPyLogrus)

    # Set root log level - higher handlers can set their own filter level
    logging.root.setLevel(logging.DEBUG)
    log = logging.getLogger(name)

    handler = None
    if log.hasHandlers():
        # The parent logger might have the handler, not this lower logger
        if len(log.handlers) > 0:
            # TODO(andy): Remove necessity to return handler or
            # correctly obtain the handler without causing an exception
            handler = log.handlers[0]
    else:
        # Add our handler
        handler = logging_handlers.SysLogHandler(address='/dev/log')
        handler.setFormatter(TextFormatter(
            fmt='%(levelname)s %(message)s', colorize=False))
        log.addHandler(handler)

    return log.with_prefix(), handler
