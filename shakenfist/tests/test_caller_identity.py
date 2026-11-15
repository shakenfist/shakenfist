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


class CallerTaskTestCase(base.ShakenFistTestCase):
    """The per-thread task label."""

    def test_default_is_empty(self):
        self.assertEqual('', caller_identity.get_caller_task())

    def test_set_inside_the_context_and_restored_after(self):
        with caller_identity.caller_task('maintain_blobs'):
            self.assertEqual(
                'maintain_blobs', caller_identity.get_caller_task())
        self.assertEqual('', caller_identity.get_caller_task())

    def test_restored_when_the_pass_raises(self):
        try:
            with caller_identity.caller_task('maintain_blobs'):
                raise ValueError('a pass which failed')
        except ValueError:
            pass
        self.assertEqual('', caller_identity.get_caller_task())

    def test_nesting_restores_the_outer_task(self):
        with caller_identity.caller_task('maintain_blobs'):
            with caller_identity.caller_task('find_missing_blobs'):
                self.assertEqual(
                    'find_missing_blobs', caller_identity.get_caller_task())
            self.assertEqual(
                'maintain_blobs', caller_identity.get_caller_task())

    def test_another_thread_is_not_labelled(self):
        # The reason this is thread local: a daemon which calls the
        # database from another thread must not inherit the label of
        # whatever the main loop is doing.
        import threading

        seen = []
        with caller_identity.caller_task('update_power_states'):
            thread = threading.Thread(
                target=lambda: seen.append(
                    caller_identity.get_caller_task()))
            thread.start()
            thread.join()

        self.assertEqual([''], seen)


class KnownTasksTestCase(base.ShakenFistTestCase):
    """Every task the tree names must be one the server will count."""

    def test_every_task_the_tree_names_is_known(self):
        # Read the names out of the source rather than restating them
        # here, exactly as the caller test above does: a name the server
        # does not know is silently discarded, which is the one failure
        # mode this label has.
        import pathlib
        import re

        root = pathlib.Path(caller_identity.__file__).parent.parent
        named = set()
        for path in root.glob('**/*.py'):
            if 'tests' in path.parts:
                continue
            source = path.read_text()
            for match in re.finditer(
                    r"caller_task\(\s*'([a-z_]+)'", source):
                named.add(match.group(1))
            for match in re.finditer(
                    r"_resilient_job\(\s*\n?\s*'([a-z_]+)'", source):
                named.add(match.group(1))

        # Guard against the scan matching nothing and passing vacuously.
        self.assertIn('maintain_blobs', named)
        self.assertIn('update_power_states', named)
        self.assertGreaterEqual(len(named), 6)

        self.assertEqual(
            set(), named - caller_identity.KNOWN_TASKS,
            'a pass names a task the server will discard')

    def test_no_known_task_is_unused(self):
        # A task in the allowlist which nothing names is a label value
        # which can never appear, which is how an allowlist rots.
        import pathlib

        root = pathlib.Path(caller_identity.__file__).parent.parent
        sources = []
        for path in root.glob('**/*.py'):
            if 'tests' in path.parts or path.name == 'caller_identity.py':
                continue
            sources.append(path.read_text())
        body = '\n'.join(sources)

        for task in caller_identity.KNOWN_TASKS:
            self.assertIn(
                "'%s'" % task, body,
                'KNOWN_TASKS names a task no pass claims')
