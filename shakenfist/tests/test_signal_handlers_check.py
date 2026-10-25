# Copyright 2026 Michael Still and contributors

"""Tests for tools/check-signal-handlers.py.

A signal handler runs on the main thread between any two of its bytecodes,
so one which logs, touches the database or takes any other lock can wait on
the code it interrupted, forever. sf-api's drain handler did (#3495), and
every daemon's exit_gracefully() could (#4601); each looked like ordinary
code. The checker refuses that at commit time, these tests keep it honest,
and the last two are the guard for the tree itself.
"""

import importlib.util
import os
import shutil
import tempfile
import textwrap

from shakenfist.tests import base


def _load_checker():
    # The checker is a standalone script rather than an importable module,
    # because it is also a pre-commit hook entry point.
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    path = os.path.join(root, 'tools', 'check-signal-handlers.py')
    spec = importlib.util.spec_from_file_location('check_signal_handlers', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


SIGNALS = textwrap.dedent('''\
    def signal_safe(func):
        return func
''')


class SignalHandlerCheckTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.write('shakenfist/util/signals.py', SIGNALS)

    def write(self, relpath, source):
        path = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(textwrap.dedent(source))

    def check(self, source, relpath='shakenfist/daemon.py'):
        self.write(relpath, source)
        c = checker.Checker(self.root)
        return c, c.run()

    def assertRefuses(self, source, fragment, relpath='shakenfist/daemon.py'):
        _, errors = self.check(source, relpath)
        self.assertEqual(1, len(errors), errors)
        self.assertIn(fragment, errors[0])
        return errors[0]

    def test_logging_in_a_handler_is_refused(self):
        error = self.assertRefuses('''\
            import signal
            LOG = None

            def handler(sig, frame):
                LOG.info('stopping')

            signal.signal(signal.SIGTERM, handler)
        ''', 'calls LOG.info()')
        self.assertTrue(error.startswith('shakenfist/daemon.py:5: '), error)

    def test_assignments_and_raw_syscalls_are_allowed(self):
        c, errors = self.check('''\
            import os
            import signal
            STOPPING = False
            SEEN = {}

            def handler(sig, frame):
                global STOPPING
                if sig == signal.SIGTERM:
                    STOPPING = True
                    SEEN['sig'] = (sig, frame)
                    fd = os.open('/run/x.abort', os.O_WRONLY | os.O_CREAT, 0o666)
                    try:
                        os.write(fd, b'1')
                    finally:
                        os.close(fd)
                    return

            signal.signal(signal.SIGTERM, handler)
        ''')
        self.assertEqual([], errors)
        self.assertEqual(['handler'], [h.name for _, h in c.handlers])

    def test_other_os_calls_are_refused(self):
        self.assertRefuses('''\
            import os
            import signal

            def handler(sig, frame):
                os.unlink('/run/x.abort')

            signal.signal(signal.SIGTERM, handler)
        ''', 'calls os.unlink()')

    def test_a_signal_safe_helper_is_allowed_and_checked(self):
        self.assertRefuses('''\
            import signal
            from shakenfist.util.signals import signal_safe
            LOG = None

            @signal_safe
            def helper():
                LOG.info('this is the regression the decorator must not hide')

            def handler(sig, frame):
                helper()

            signal.signal(signal.SIGTERM, handler)
        ''', '@signal_safe helper() calls LOG.info()')

    def test_an_unmarked_helper_is_refused(self):
        self.assertRefuses('''\
            import signal

            def helper():
                pass

            def handler(sig, frame):
                helper()

            signal.signal(signal.SIGTERM, handler)
        ''', 'calls helper()')

    def test_helpers_in_other_modules_are_followed(self):
        self.write('shakenfist/external_api/health.py', '''\
            from shakenfist.util import signals
            draining = False
            LOG = None

            @signals.signal_safe
            def begin_drain():
                global draining
                draining = True
                LOG.info('begin')
        ''')
        error = self.assertRefuses('''\
            import signal
            from shakenfist.external_api import health

            def handler(sig, frame):
                health.begin_drain()

            signal.signal(signal.SIGTERM, handler)
        ''', 'calls LOG.info()', relpath='shakenfist/external_api/gunicorn_config.py')
        self.assertTrue(error.startswith('shakenfist/external_api/health.py:9: '), error)

    def test_a_waiver_needs_a_reason(self):
        _, errors = self.check('''\
            import signal
            import threading
            EXIT = threading.Event()

            def handler(sig, frame):
                EXIT.set()  # signal-safe: the main thread only calls is_set()

            signal.signal(signal.SIGTERM, handler)
        ''')
        self.assertEqual([], errors)

        self.assertRefuses('''\
            import signal
            import threading
            EXIT = threading.Event()

            def handler(sig, frame):
                EXIT.set()  # signal-safe:

            signal.signal(signal.SIGTERM, handler)
        ''', 'calls EXIT.set()')

    def test_with_and_loops_are_refused(self):
        self.assertRefuses('''\
            import signal

            def handler(sig, frame):
                with open('/run/x.abort', 'w'):
                    pass

            signal.signal(signal.SIGTERM, handler)
        ''', 'uses a With statement')
        self.assertRefuses('''\
            import signal

            def handler(sig, frame):
                while True:
                    pass

            signal.signal(signal.SIGTERM, handler)
        ''', 'uses a While statement')

    def test_a_handler_it_cannot_find_is_an_error(self):
        self.assertRefuses('''\
            import signal

            signal.signal(signal.SIGTERM, lambda sig, frame: None)
        ''', 'cannot be found to check')

    def test_default_and_ignore_dispositions_are_not_handlers(self):
        _, errors = self.check('''\
            import signal

            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        ''')
        self.assertEqual([], errors)

    def test_nested_handlers_are_found(self):
        self.assertRefuses('''\
            import signal
            LOG = None

            def post_worker_init(worker):
                def drain(sig, frame):
                    LOG.info('draining')
                signal.signal(signal.SIGTERM, drain)
        ''', 'signal handler drain() calls LOG.info()')

    def test_methods_and_subclass_overrides_are_checked(self):
        self.write('shakenfist/daemons/queues.py', '''\
            from shakenfist import daemon

            class Monitor(daemon.WorkerPool):
                def exit_gracefully(self, sig, frame):
                    self.log.info('override')
        ''')
        c, errors = self.check('''\
            import signal

            class Daemon:
                def __init__(self):
                    signal.signal(signal.SIGTERM, self.exit_gracefully)

                def exit_gracefully(self, sig, frame):
                    self.stopping = True

            class WorkerPool(Daemon):
                pass
        ''')
        self.assertEqual(1, len(errors), errors)
        self.assertTrue(errors[0].startswith('shakenfist/daemons/queues.py:5: '), errors)
        self.assertEqual(2, len(c.handlers))

    def test_tests_are_not_checked(self):
        _, errors = self.check('''\
            import signal
            LOG = None

            def handler(sig, frame):
                LOG.info('a test may do as it likes')

            signal.signal(signal.SIGTERM, handler)
        ''', relpath='shakenfist/tests/test_something.py')
        self.assertEqual([], errors)


class SignalHandlersInTheTreeTestCase(base.ShakenFistTestCase):
    def test_the_tree_has_no_unsafe_signal_handlers(self):
        self.assertEqual([], checker.Checker().run())

    def test_the_checker_finds_the_trees_handlers(self):
        # A checker which finds nothing passes everything, so pin what it must
        # find. Add a handler here when you add one to the tree.
        c = checker.Checker()
        c.run()
        found = {(os.path.relpath(m.path, c.root), h.name) for m, h in c.handlers}
        self.assertEqual({
            ('shakenfist/daemons/daemon.py', 'exit_gracefully'),
            ('shakenfist/daemons/nodelock/main.py', 'exit_gracefully'),
            ('shakenfist/daemons/privexec/main.py', 'exit_gracefully'),
            ('shakenfist/daemons/sentinel_first/main.py', 'exit_gracefully'),
            ('shakenfist/daemons/sentinel_last/main.py', 'exit_gracefully'),
            ('shakenfist/external_api/gunicorn_config.py', '_drain_then_exit'),
        }, found)
