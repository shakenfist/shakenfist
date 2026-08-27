# Copyright 2026 Michael Still and contributors

"""Tests for console entry point logging configuration.

logs.setup_console() only attaches a handler to the entry point module's own
logger. Each console entry point must therefore also configure the root
logger (or log lines from every other module are dropped) and disable
propagation on its own logger (or its own lines are emitted twice once the
root logger has a handler). See the console-logging consistency audit.
"""

import importlib
import logging
import sys
from unittest import mock

import click

from shakenfist.tests import base
from shakenfist.util import caller_identity


CONSOLE_ENTRY_POINTS = [
    'shakenfist.client.backup',
    'shakenfist.client.ctl',
]


class ConsoleLoggingTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(logging.root.setLevel, logging.root.level)
        # ctl's cli group records the process caller identity, which is
        # process-global state; restore it so other tests are unaffected.
        self.addCleanup(caller_identity.set_caller_identity,
                        caller_identity.get_caller_daemon())

    def _import_fresh(self, name):
        # ctl runs verify_config() at import time, which exits in a test
        # environment, so bypass it. The module is re-imported so its
        # import-time logging setup runs under this test.
        if name in sys.modules:
            del sys.modules[name]
        with mock.patch('shakenfist.config.verify_config'):
            return importlib.import_module(name)

    def test_import_configures_root_logger(self):
        for name in CONSOLE_ENTRY_POINTS:
            saved_handlers = logging.root.handlers[:]
            logging.root.handlers = []
            try:
                self._import_fresh(name)
                self.assertTrue(
                    logging.root.handlers,
                    '%s left the root logger without a handler, so log '
                    'lines from other modules are dropped' % name)
                self.assertFalse(
                    logging.getLogger(name).propagate,
                    '%s still propagates to the root logger, so its own '
                    'lines are emitted twice' % name)
            finally:
                logging.root.handlers = saved_handlers

    def test_verbose_lowers_root_level(self):
        for name in CONSOLE_ENTRY_POINTS:
            mod = self._import_fresh(name)
            logging.root.setLevel(logging.INFO)
            with click.Context(mod.cli):
                mod.cli.callback(verbose=True)
            self.assertEqual(
                logging.DEBUG, logging.root.level,
                '%s --verbose did not lower the root logger level, so '
                'debug lines from other modules are dropped' % name)
