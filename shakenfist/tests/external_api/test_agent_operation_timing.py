# Copyright 2019 Michael Still and contributors
#
# Unit tests for api_base.agent_operation_timing(), which turns the two
# timing parameters on the agent creating endpoints into the values
# stored on the operation.
#
# The whole point of the helper is that each parameter has three
# meanings, not two: omitted (apply the server default), an explicit 0
# (the caller asked for none), and a count of seconds. Python makes the
# first two easy to conflate, because 0 is falsy, and conflating them
# silently gives a caller who asked for no deadline a 600 second one.

import json
import math
from unittest import mock

from shakenfist.config import config
from shakenfist.external_api import base as api_base
from shakenfist.tests import base


NOW = 1787427490.5


class AgentOperationTimingTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.time = mock.patch('shakenfist.external_api.base.time.time',
                               return_value=NOW)
        self.time.start()
        self.addCleanup(self.time.stop)

    def _timing(self, deadline_seconds=None, progress_timeout_seconds=None,
                progress_capable=True):
        return api_base.agent_operation_timing(
            deadline_seconds, progress_timeout_seconds, progress_capable)

    def _error(self, response):
        self.assertEqual(400, response.status_code)
        return json.loads(response.data)['error']

    def test_omitted_applies_the_defaults(self):
        (deadline, progress_timeout), error = self._timing()
        self.assertIsNone(error)
        self.assertEqual(
            NOW + config.AGENT_OPERATION_DEFAULT_DEADLINE, deadline)
        self.assertEqual(
            float(config.AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT),
            progress_timeout)

    def test_omitted_reads_the_config_rather_than_a_literal(self):
        # Proves the config options are actually consulted. Without
        # this the defaults could be hardcoded and every other test
        # here would still pass.
        with mock.patch.object(
                config, 'AGENT_OPERATION_DEFAULT_DEADLINE', 42), \
            mock.patch.object(
                config, 'AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT', 7):
            (deadline, progress_timeout), error = self._timing()
        self.assertIsNone(error)
        self.assertEqual(NOW + 42, deadline)
        self.assertEqual(7.0, progress_timeout)

    def test_omitted_progress_timeout_is_zero_when_not_progress_capable(self):
        # The execute endpoint. A default here could never fire, because
        # no command it builds reports progress, so recording one would
        # describe the operation as something it is not.
        (deadline, progress_timeout), error = self._timing(
            progress_capable=False)
        self.assertIsNone(error)
        self.assertEqual(
            NOW + config.AGENT_OPERATION_DEFAULT_DEADLINE, deadline)
        self.assertEqual(0.0, progress_timeout)

    def test_explicit_zero_deadline_is_not_omitted(self):
        # 0 is falsy, and treating it as "unset" would hand a caller who
        # asked for no wall-clock deadline a 600 second one. The
        # no-deadline-plus-progress-timeout combination is the reason
        # the sentinel exists.
        (deadline, progress_timeout), error = self._timing(
            deadline_seconds=0, progress_timeout_seconds=30)
        self.assertIsNone(error)
        self.assertEqual(0.0, deadline)
        self.assertEqual(30.0, progress_timeout)

    def test_explicit_zero_progress_timeout_is_not_omitted(self):
        (deadline, progress_timeout), error = self._timing(
            deadline_seconds=60, progress_timeout_seconds=0)
        self.assertIsNone(error)
        self.assertEqual(NOW + 60, deadline)
        self.assertEqual(0.0, progress_timeout)

    def test_both_explicitly_zero(self):
        (deadline, progress_timeout), error = self._timing(
            deadline_seconds=0, progress_timeout_seconds=0)
        self.assertIsNone(error)
        self.assertEqual(0.0, deadline)
        self.assertEqual(0.0, progress_timeout)

    def test_values_are_seconds_from_now(self):
        (deadline, progress_timeout), error = self._timing(
            deadline_seconds=90, progress_timeout_seconds=15)
        self.assertIsNone(error)
        self.assertEqual(NOW + 90, deadline)
        self.assertEqual(15.0, progress_timeout)

    def test_fractional_seconds_survive(self):
        (deadline, _), error = self._timing(deadline_seconds=0.25)
        self.assertIsNone(error)
        self.assertEqual(NOW + 0.25, deadline)

    def test_a_supplied_progress_timeout_survives_a_non_capable_operation(self):
        # The caller is never silently overruled: only an *omitted*
        # value is decided by progress_capable.
        (_, progress_timeout), error = self._timing(
            progress_timeout_seconds=5, progress_capable=False)
        self.assertIsNone(error)
        self.assertEqual(5.0, progress_timeout)

    def test_a_deadline_above_the_ceiling_is_refused(self):
        # The operator ceiling is what the published maximum on
        # deadline_seconds is backed by (issue #4074).
        values, error = self._timing(
            deadline_seconds=config.AGENT_OPERATION_MAX_DEADLINE + 1)
        self.assertIsNone(values)
        self.assertIn('deadline_seconds', self._error(error))
        self.assertIn('AGENT_OPERATION_MAX_DEADLINE', self._error(error))

    def test_a_progress_timeout_above_the_ceiling_is_refused(self):
        # The same ceiling bounds both parameters: with no wall-clock
        # deadline, an enormous progress timeout parks the executor
        # slot just as effectively as an enormous deadline would.
        values, error = self._timing(
            progress_timeout_seconds=config.AGENT_OPERATION_MAX_DEADLINE + 1)
        self.assertIsNone(values)
        self.assertIn('progress_timeout_seconds', self._error(error))

    def test_the_ceiling_itself_is_accepted(self):
        # The bound is inclusive, matching the published maximum.
        (deadline, progress_timeout), error = self._timing(
            deadline_seconds=config.AGENT_OPERATION_MAX_DEADLINE,
            progress_timeout_seconds=config.AGENT_OPERATION_MAX_DEADLINE)
        self.assertIsNone(error)
        self.assertEqual(NOW + config.AGENT_OPERATION_MAX_DEADLINE, deadline)
        self.assertEqual(
            float(config.AGENT_OPERATION_MAX_DEADLINE), progress_timeout)

    def test_the_ceiling_reads_the_config_rather_than_a_literal(self):
        with mock.patch.object(config, 'AGENT_OPERATION_MAX_DEADLINE', 50):
            values, error = self._timing(deadline_seconds=51)
            self.assertIsNone(values)
            self.assertEqual(400, error.status_code)

            (deadline, _), error = self._timing(deadline_seconds=50)
            self.assertIsNone(error)
            self.assertEqual(NOW + 50, deadline)

    def test_the_zero_sentinel_passes_the_ceiling(self):
        # 0 is a sentinel rather than a duration, and the enforcement
        # side (AgentOperation.effective_deadline()) is what decides
        # whether it really means unbounded, so the API must not refuse
        # it however low the operator sets the ceiling.
        (deadline, _), error = self._timing(
            deadline_seconds=0, progress_timeout_seconds=30)
        self.assertIsNone(error)
        self.assertEqual(0.0, deadline)

    def test_negative_deadline_is_refused(self):
        values, error = self._timing(deadline_seconds=-1)
        self.assertIsNone(values)
        self.assertIn('deadline_seconds', self._error(error))
        self.assertIn('negative', self._error(error))

    def test_negative_progress_timeout_is_refused(self):
        values, error = self._timing(progress_timeout_seconds=-0.5)
        self.assertIsNone(values)
        self.assertIn('progress_timeout_seconds', self._error(error))

    def test_non_numeric_is_refused(self):
        values, error = self._timing(deadline_seconds='soon')
        self.assertIsNone(values)
        self.assertIn('number of seconds', self._error(error))

    def test_a_list_is_refused(self):
        values, error = self._timing(deadline_seconds=[60])
        self.assertIsNone(values)
        self.assertIn('number of seconds', self._error(error))

    def test_a_boolean_is_refused(self):
        # isinstance(True, int) is true, so float(True) is 1.0 and a
        # JSON true would otherwise be accepted as a one second
        # deadline.
        values, error = self._timing(deadline_seconds=True)
        self.assertIsNone(values)
        self.assertIn('boolean', self._error(error))

    def test_nan_is_refused(self):
        # NaN fails every comparison including "nan < 0", so a bare
        # negativity test would let it through and store a deadline
        # nothing can ever be later than.
        values, error = self._timing(deadline_seconds=math.nan)
        self.assertIsNone(values)
        self.assertIn('finite', self._error(error))

    def test_infinity_is_refused(self):
        # Infinity is non-negative, so it passes a negativity test.
        # An infinite deadline means the same thing as the 0 sentinel
        # while looking like a duration, and the DOUBLE column it
        # would be written to cannot represent it.
        values, error = self._timing(deadline_seconds=math.inf)
        self.assertIsNone(values)
        self.assertIn('finite', self._error(error))

    def test_infinity_as_a_string_is_refused(self):
        # This is the vector that reaches a running server: "inf" is
        # an ordinary JSON string which float() happily converts, and
        # json.loads() accepts the bare Infinity literal as well.
        values, error = self._timing(deadline_seconds='inf')
        self.assertIsNone(values)
        self.assertIn('finite', self._error(error))

    def test_a_negative_infinity_is_refused(self):
        values, error = self._timing(progress_timeout_seconds=-math.inf)
        self.assertIsNone(values)
        self.assertIn('finite', self._error(error))

    def test_the_first_bad_parameter_is_reported(self):
        # Both are wrong; the message must name one of them rather than
        # a generic complaint, so a caller can tell which to fix.
        values, error = self._timing(
            deadline_seconds=-1, progress_timeout_seconds=-1)
        self.assertIsNone(values)
        self.assertIn('deadline_seconds', self._error(error))
