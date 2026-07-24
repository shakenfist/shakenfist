# Copyright 2026 Michael Still and contributors

"""Tests for the process-global caller identity."""

from shakenfist.tests import base
from shakenfist.util import caller_identity


class CallerIdentityTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        # The identity is a process-global; restore it after each test.
        original = caller_identity.get_caller_daemon()
        self.addCleanup(caller_identity.set_caller_identity, original)

    def test_set_and_get(self):
        caller_identity.set_caller_identity('queues')
        self.assertEqual('queues', caller_identity.get_caller_daemon())

    def test_default_when_unset(self):
        # A process that never set an identity reports 'unknown'.
        caller_identity.set_caller_identity('unknown')
        self.assertEqual('unknown', caller_identity.get_caller_daemon())
