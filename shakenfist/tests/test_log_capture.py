import gc
import io
import logging
import sys
from unittest import mock
import weakref

import testtools
from testtools.testresult.doubles import ExtendedTestResult

from shakenfist.tests import base
from shakenfist.tests import log_capture


class LogCaptureTestCase(base.ShakenFistTestCase):
    """The stdout log handlers are captured per test (issue #4991)."""

    def setUp(self):
        super().setUp()
        # Stand in for the process's stdout, so that a capture which
        # failed would show up here rather than in the subunit stream.
        self.stdout = io.StringIO()
        patcher = mock.patch.object(sys, 'stdout', self.stdout)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Bound the way shakenfist_utilities.logs.setup() binds it: to
        # whatever sys.stdout is when the handler is made.
        self.handler = logging.StreamHandler(sys.stdout)
        self.handler.setFormatter(logging.Formatter('%(message)s'))
        self.logger = logging.getLogger('shakenfist.tests.log_capture_probe')
        self.logger.propagate = False
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self.handler)
        self.addCleanup(self.logger.removeHandler, self.handler)

    def _run(self, body):
        logger = self.logger

        class Inner(testtools.TestCase):
            def setUp(self):
                super().setUp()
                self.buffer = log_capture.capture(self)

            def test(self):
                logger.info('a record from the test')
                body(self)

        case = Inner('test')
        result = ExtendedTestResult()
        case.run(result)
        return case, result

    def _details(self, result, outcome):
        events = [e for e in result._events if e[0] == outcome]
        self.assertEqual(1, len(events), result._events)
        # An outcome reported without details is recorded without them.
        return events[0][2] if len(events[0]) > 2 else {}

    def test_a_passing_test_writes_nothing_to_stdout(self):
        case, result = self._run(lambda inner: None)
        self.assertEqual('', self.stdout.getvalue())
        self.assertEqual('a record from the test\n', case.buffer.getvalue())
        self.assertNotIn('log', self._details(result, 'addSuccess'))

    def test_the_handler_is_restored_afterwards(self):
        self._run(lambda inner: None)
        self.assertIs(self.stdout, self.handler.stream)
        self.logger.info('after the test')
        self.assertEqual('after the test\n', self.stdout.getvalue())

    def test_a_failing_test_carries_its_log(self):
        _, result = self._run(lambda inner: inner.fail('no'))
        self.assertEqual('', self.stdout.getvalue())
        details = self._details(result, 'addFailure')
        self.assertEqual('a record from the test\n',
                         details['log'].as_text())

    def test_an_erroring_test_carries_its_log(self):
        def body(inner):
            raise RuntimeError('boom')

        _, result = self._run(body)
        details = self._details(result, 'addError')
        self.assertEqual('a record from the test\n',
                         details['log'].as_text())

    def test_a_skipped_test_does_not_carry_its_log(self):
        _, result = self._run(lambda inner: inner.skipTest('later'))
        self.assertNotIn('log', self._details(result, 'addSkip'))

    def test_a_handler_on_stderr_is_captured_too(self):
        # As logging.basicConfig() installs on the root logger.
        stderr = io.StringIO()
        patcher = mock.patch.object(sys, 'stderr', stderr)
        patcher.start()
        self.addCleanup(patcher.stop)
        handler = logging.StreamHandler(sys.stderr)
        self.logger.addHandler(handler)
        self.addCleanup(self.logger.removeHandler, handler)

        case, _ = self._run(lambda inner: None)
        self.assertEqual('', stderr.getvalue())
        self.assertIs(stderr, handler.stream)
        # One record, once through each of the two handlers.
        self.assertEqual(2, case.buffer.getvalue().count(
            'a record from the test'))

    def test_a_finished_test_is_freed_without_the_cycle_collector(self):
        # A capture which held its case strongly would make every test
        # instance cyclic garbage, keeping what it allocated resident
        # into the next test, where the memory guard would blame that one.
        gc.disable()
        self.addCleanup(gc.enable)
        case, result = self._run(lambda inner: None)
        ref = weakref.ref(case)
        del case, result
        self.assertIsNone(ref())

    def test_a_handler_on_another_stream_is_left_alone(self):
        other = io.StringIO()
        handler = logging.StreamHandler(other)
        self.logger.addHandler(handler)
        self.addCleanup(self.logger.removeHandler, handler)

        self._run(lambda inner: None)
        self.assertEqual('a record from the test\n', other.getvalue())
        self.assertIs(other, handler.stream)
