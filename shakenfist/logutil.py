import copy
import logging
from logging import handlers as logging_handlers
import os
from pylogrus import TextFormatter
from pylogrus.base import PyLogrusBase
import re
import setproctitle
import traceback

from shakenfist import config


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

    def withField(self, key, value):
        return SFCustomAdapter(self, {key: value})

    #
    # Convenience methods
    #
    def withObj(self, object):
        if not object:
            return SFCustomAdapter(self, {})
        try:
            label, value = object.unique_label()
        except Exception as e:
            raise Exception('Bad object - no unique_label() function: %s' % e)
        return SFCustomAdapter(self, {label: value})

    #
    # Use labelled convenience methods when ID is a string (not object)
    # Note: the helper method still handles objects
    #
    def withInstance(self, inst):
        if not isinstance(inst, str):
            inst = inst.db_entry['uuid']
        return SFCustomAdapter(self, {'instance': inst})

    def withNetwork(self, inst):
        if not isinstance(inst, str):
            inst = inst.uuid
        return SFCustomAdapter(self, {'network': inst})

    def withNetworkInterface(self, inst):
        if not isinstance(inst, str):
            inst = inst.uuid
        return SFCustomAdapter(self, {'networkinterface': inst})

    def withImage(self, inst):
        if not isinstance(inst, str):
            inst = inst.hashed_image_url
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

    def withFields(self, fields=None):
        extra = copy.deepcopy(self._extra)
        extra.update(self._normalize(fields))
        return SFCustomAdapter(self._logger, extra, self._prefix)

    def withField(self, key, value):
        extra = copy.deepcopy(self._extra)
        extra.update(self._normalize({key: value}))
        return SFCustomAdapter(self._logger, extra, self._prefix)

    def withPrefix(self, prefix=None):
        return self if prefix is None else SFCustomAdapter(self._logger, self._extra, prefix)

    FILENAME_RE = re.compile('.*/dist-packages/shakenfist/(.*)')

    def process(self, msg, kwargs):
        msg = '%s[%s] %s' % (setproctitle.getproctitle(), os.getpid(), msg)
        kwargs["extra"] = self.extra

        if config.parsed.get('LOG_METHOD_TRACE'):
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
    def withObj(self, object):
        extra = copy.deepcopy(self._extra)
        if object:
            try:
                label, value = object.unique_label()
            except Exception as e:
                raise Exception(
                    'Bad object - no unique_label() function: %s' % e)
            extra.update({label: value})
        return SFCustomAdapter(self._logger, extra, self._prefix)

    #
    # Use labelled convenience methods when ID is a string (not object)
    # Note: the helper method still handles objects
    #
    def withInstance(self, inst):
        extra = copy.deepcopy(self._extra)
        if not isinstance(inst, str):
            inst = inst.db_entry['uuid']
        extra.update({'instance': inst})
        return SFCustomAdapter(self._logger, extra, self._prefix)

    def withNetwork(self, inst):
        extra = copy.deepcopy(self._extra)
        if not isinstance(inst, str):
            inst = inst.uuid
        extra.update({'network': inst})
        return SFCustomAdapter(self._logger, extra, self._prefix)

    def withNetworkInterface(self, inst):
        extra = copy.deepcopy(self._extra)
        if not isinstance(inst, str):
            inst = inst.uuid
        extra.update({'networkinterface': inst})
        return SFCustomAdapter(self._logger, extra, self._prefix)

    def withImage(self, inst):
        extra = copy.deepcopy(self._extra)
        if not isinstance(inst, str):
            inst = inst.hashed_image_url
        extra.update({'image': inst})
        return SFCustomAdapter(self._logger, extra, self._prefix)


def setup(name):
    logging.setLoggerClass(SFPyLogrus)

    # Set root log level - higher handlers can set their own filter level
    logging.root.setLevel(logging.DEBUG)
    log = logging.getLogger(name)

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

    return log.withPrefix(), handler
