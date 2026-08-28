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


class KnownCallersTestCase(base.ShakenFistTestCase):
    """The allowlist that keeps the metrics label bounded.

    KNOWN_CALLERS is duplicated from daemon.DAEMON_NAMES because
    caller_identity.py must not import daemon.py. These pin the two
    together so the duplication cannot drift.
    """

    def test_every_daemon_name_is_a_known_caller(self):
        from shakenfist.daemons import daemon

        self.assertEqual(
            set(), set(daemon.DAEMON_NAMES) - caller_identity.KNOWN_CALLERS,
            'a daemon whose name is not in KNOWN_CALLERS would have its '
            'database load attributed to "unknown"')

    def test_every_identity_the_tree_claims_is_a_known_caller(self):
        # Several processes do not go through Daemon and claim an identity
        # by literal, so DAEMON_NAMES alone does not cover them. Read them
        # out of the source rather than restating the list here.
        import pathlib
        import re

        root = pathlib.Path(caller_identity.__file__).parent.parent
        claimed = set()
        for path in root.glob('**/*.py'):
            if 'tests' in path.parts:
                continue
            for match in re.finditer(
                    r"set_caller_identity\(\s*'([a-z-]+)'", path.read_text()):
                claimed.add(match.group(1))

        # Guard against the scan matching nothing and passing vacuously.
        self.assertIn('database', claimed)
        self.assertIn('sentinel-first', claimed)
        self.assertGreaterEqual(len(claimed), 6)

        self.assertEqual(
            set(), claimed - caller_identity.KNOWN_CALLERS,
            'a process claims an identity the server will discard')
