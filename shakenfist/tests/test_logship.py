# Copyright 2026 Michael Still and contributors
"""Unit tests for the Loki log shipper handler and its lifecycle.

Covers the handler's ``emit`` (valid JSON, clean message, enqueue,
exception-safety) and ``start()``'s Mode A / Mode B handler
attachment behaviour. Assertions are version-agnostic: under the
installed v0.8.4 the formatter is the fallback ``JsonFormatter``;
under v0.9.0 it is the library's instance. We assert *type*
(``JsonFormatter``), never identity.
"""
import json
import logging
import uuid
from logging.handlers import SysLogHandler
from unittest import mock

from shakenfist_utilities.logs import JsonFormatter

from shakenfist import logship
from shakenfist import logship_spool
from shakenfist.tests import base


class _SpoolRootMixin(base.SpoolRootMixin):
    spool_module = logship_spool
    spool_prefix = 'sf-logship-test-'
    extra_resets = (logship.reset_for_tests,)


class HandlerEmitTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """``LokiHandler.emit`` formats, computes ts_ns, and enqueues."""

    def setUp(self):
        super().setUp()
        logship_spool.initialise('test-daemon')
        self.handler = logship.LokiHandler()
        self.handler.setFormatter(logship._build_formatter())

    def _record(self, msg='hello world'):
        return logging.LogRecord(
            name='shakenfist.test', level=logging.INFO,
            pathname=__file__, lineno=1, msg=msg, args=(),
            exc_info=None)

    def test_emit_enqueues_valid_json_with_clean_message(self):
        self.handler.emit(self._record('a clean message'))

        spool = logship_spool.get_spool()
        batch = spool.dequeue_batch(10)
        self.assertEqual(1, len(batch))
        _id, ts_ns, line = batch[0]
        self.assertIsInstance(ts_ns, int)
        self.assertGreater(ts_ns, 0)
        # The line is valid JSON with the message intact.
        parsed = json.loads(line)
        self.assertEqual('a clean message', parsed['message'])

    def test_emit_uses_record_created_for_ts_ns(self):
        record = self._record()
        record.created = 1234.5
        self.handler.emit(record)
        batch = logship_spool.get_spool().dequeue_batch(1)
        self.assertEqual(int(1234.5 * 1_000_000_000), batch[0][1])

    def test_emit_does_not_propagate_enqueue_errors(self):
        # An enqueue failure must route through handleError and not
        # raise into the logging call site.
        with mock.patch(
                'shakenfist.logship_spool.enqueue',
                side_effect=RuntimeError('boom')), \
                mock.patch.object(self.handler, 'handleError') as he:
            # Must not raise.
            self.handler.emit(self._record())
        he.assert_called_once()

    def test_emit_ships_record_with_unserialisable_extra_fields(self):
        # Issue 3573: a raw uuid.UUID in the record's extra_fields made
        # format() raise TypeError, routing the record to handleError (a
        # ~60 line raw traceback in syslog) and dropping it. The handler
        # must instead stringify the offending field and ship the record.
        net_uuid = uuid.UUID('12345678-1234-5678-1234-567812345678')
        record = self._record('added event with uuid extra')
        record.extra_fields = {
            'extra': {'network_uuid': net_uuid},
            'plain': 'untouched',
        }

        with mock.patch.object(self.handler, 'handleError') as he:
            self.handler.emit(record)
        he.assert_not_called()

        batch = logship_spool.get_spool().dequeue_batch(10)
        self.assertEqual(1, len(batch))
        parsed = json.loads(batch[0][2])
        self.assertEqual('added event with uuid extra', parsed['message'])
        self.assertEqual(str(net_uuid), parsed['extra']['network_uuid'])
        self.assertEqual('untouched', parsed['plain'])

    def test_emit_ships_record_when_default_str_also_fails(self):
        # A dict with a non-string key can't be encoded even with a
        # default= fallback (keys don't pass through default). The last
        # resort stringifies the whole field value; the record still ships.
        record = self._record('pathological extra')
        record.extra_fields = {'weird': {uuid.uuid4(): 'value'}}

        with mock.patch.object(self.handler, 'handleError') as he:
            self.handler.emit(record)
        he.assert_not_called()

        batch = logship_spool.get_spool().dequeue_batch(10)
        self.assertEqual(1, len(batch))
        parsed = json.loads(batch[0][2])
        self.assertEqual('pathological extra', parsed['message'])
        self.assertIsInstance(parsed['weird'], str)


class FormatterTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """``_build_formatter`` reuse vs. fallback."""

    def test_fallback_when_no_library_json_formatter(self):
        # With no JsonFormatter installed anywhere, the fallback
        # constructs a fresh one (the v0.8.4 case).
        with mock.patch.object(
                logship, '_find_library_json_formatter',
                return_value=None):
            fmt = logship._build_formatter()
        self.assertIsInstance(fmt, JsonFormatter)

    def test_reuses_existing_library_json_formatter(self):
        sentinel = JsonFormatter(datefmt='Z', enabled_fields=['message'])
        with mock.patch.object(
                logship, '_find_library_json_formatter',
                return_value=sentinel):
            fmt = logship._build_formatter()
        self.assertIs(sentinel, fmt)


class StartModeTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """Mode A / Mode B handler-attachment behaviour."""

    def setUp(self):
        super().setUp()
        # Build an isolated logger tree we control: a per-module
        # logger carrying a SysLogHandler, mirroring what the
        # library installs at import time.
        self.module_logger = logging.getLogger(
            'shakenfist.tests._logship_fake_module')
        self._syslog = SysLogHandler(address=('localhost', 0))
        # Don't actually open a socket connection during the test.
        self._syslog.socket = mock.Mock()
        self.module_logger.addHandler(self._syslog)
        self.addCleanup(self.module_logger.removeHandler, self._syslog)

        self.root = logging.getLogger('')
        self._root_handlers_before = list(self.root.handlers)

    def _root_loki_handlers(self):
        return [h for h in self.root.handlers
                if isinstance(h, logship.LokiHandler)]

    def test_mode_b_no_base_url_is_noop(self):
        with mock.patch.object(logship.config, 'LOKI_BASE_URL', ''), \
                mock.patch(
                    'shakenfist.logship_drainer.start') as drainer_start:
            logship.start('test-daemon')

        # No Loki handler attached to root.
        self.assertEqual([], self._root_loki_handlers())
        # The per-module SysLogHandler is left in place.
        self.assertIn(self._syslog, self.module_logger.handlers)
        # No drainer thread started.
        drainer_start.assert_not_called()

    def test_mode_a_attaches_loki_to_root_at_info_keeps_local(self):
        with mock.patch.object(
                logship.config, 'LOKI_BASE_URL', 'http://loki:3100'), \
                mock.patch(
                    'shakenfist.logship_drainer.start') as drainer_start:
            logship.start('test-daemon')

        # The per-module SysLogHandler is LEFT in place, so DEBUG and
        # everything else still logs locally to journald.
        self.assertIn(self._syslog, self.module_logger.handlers)
        # Exactly one Loki handler is attached to root.
        loki_handlers = self._root_loki_handlers()
        self.assertEqual(1, len(loki_handlers))
        # Its formatter is a JsonFormatter (reuse or fallback).
        self.assertIsInstance(loki_handlers[0].formatter, JsonFormatter)
        # It only ships INFO and above; DEBUG stays local.
        self.assertEqual(logging.INFO, loki_handlers[0].level)
        # The drainer was started.
        drainer_start.assert_called_once_with('test-daemon')

    def test_debug_records_are_not_shipped_to_loki(self):
        # With the Loki handler at INFO level, a DEBUG record that
        # propagates to root must not reach the spool, while an INFO
        # record must.
        with mock.patch.object(
                logship.config, 'LOKI_BASE_URL', 'http://loki:3100'), \
                mock.patch('shakenfist.logship_drainer.start'):
            logship.start('test-daemon')

        enqueued = []
        with mock.patch(
                'shakenfist.logship_spool.enqueue',
                side_effect=lambda ts, line: enqueued.append((ts, line))):
            # A logger that propagates to root (where the Loki handler is).
            log = logging.getLogger('shakenfist.test.levels')
            log.setLevel(logging.DEBUG)
            log.debug('a debug line')
            log.info('an info line')

        shipped = ' '.join(line for _, line in enqueued)
        self.assertNotIn('a debug line', shipped)
        self.assertIn('an info line', shipped)

    def test_mode_a_is_idempotent(self):
        with mock.patch.object(
                logship.config, 'LOKI_BASE_URL', 'http://loki:3100'), \
                mock.patch('shakenfist.logship_drainer.start'):
            logship.start('test-daemon')
            logship.start('test-daemon')

        # Still exactly one Loki handler on root.
        self.assertEqual(1, len(self._root_loki_handlers()))
