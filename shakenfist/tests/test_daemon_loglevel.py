# Copyright 2019 Michael Still and contributors
import logging
from unittest import mock

from shakenfist.config import BaseSettings
from shakenfist.daemons import daemon
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    # error rather than debug so the tests can tell package-level
    # inheritance apart from the DEBUG root logger logs.setup()
    # leaves behind.
    LOGLEVEL_NET: str = 'error'
    LOGLEVEL_QUEUES: str = 'bogus'


fake_config = FakeConfig()


class ApplyLogLevelTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.config = mock.patch(
            'shakenfist.daemons.daemon.config', fake_config)
        self.config.start()
        self.addCleanup(self.config.stop)

        pkg = logging.getLogger('shakenfist')
        self.addCleanup(pkg.setLevel, pkg.level)

    def test_configured_level_applies_to_package(self):
        daemon.apply_log_level('net')
        self.assertEqual(
            logging.ERROR, logging.getLogger('shakenfist').level)

        # Module loggers have no explicit level, so they must inherit
        # the package level rather than the root logger's DEBUG.
        self.assertEqual(
            logging.ERROR,
            logging.getLogger(
                'shakenfist.util.concurrency').getEffectiveLevel())

    def test_unconfigured_daemon_defaults_to_info(self):
        # FakeConfig has no LOGLEVEL_PRIVEXEC.
        daemon.apply_log_level('privexec')
        self.assertEqual(
            logging.INFO, logging.getLogger('shakenfist').level)

    def test_invalid_level_raises(self):
        self.assertRaises(
            ValueError, daemon.apply_log_level, 'queues')
