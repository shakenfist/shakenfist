# Copyright 2019 Michael Still and contributors
"""Unit tests for ``BaseTestCase._assert_power_state()``.

The helper reads ``system_client.get_instance()['power_state']`` exactly
once and fails without waiting or retrying (D1 in
docs/plans/PLAN-power-state-correctness-phase-00-assertions.md explains
why a wait would hide real faults rather than avoid flakes). These tests
exercise that contract directly, unbound from the rest of the functional
suite.

``shakenfist_ci/base.py`` is loaded by path, with ``shakenfist_client``
and ``prettytable`` stubbed out, because neither is a test dependency of
this repository. This is the same loader ``test_ci_capacity_wait.py`` and
``test_ci_assert_refused_at_stage.py`` each carry their own copy of; a
third copy is written here rather than imported from either, following
their precedent of not sharing it across test modules.
"""

import logging
import os
import sys
import types
from unittest import mock

from shakenfist.tests import base as test_base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')


def _client_stubs():
    """A shakenfist_client stub with just the exception taxonomy.

    ``base.py`` references several of these names at module level (the
    tuple of agent exceptions near the top of the file), so the stub
    must define them even though this test never raises one.
    """
    client = types.ModuleType('shakenfist_client')
    apiclient = types.ModuleType('shakenfist_client.apiclient')

    class APIException(Exception):
        def __init__(self, message='', method=None, url=None,
                     status_code=None, text=None):
            super().__init__(message)
            self.status_code = status_code
            self.text = text

    apiclient.APIException = APIException
    for name in ('InsufficientResourcesException',
                 'ResourceStateConflictException',
                 'ResourceNotFoundException',
                 'RequestMalformedException',
                 'ServiceUnavailableException',
                 'IncapableException',
                 'AgentCommandError',
                 'AgentOperationFailed',
                 'AgentAwaitTimeout'):
        setattr(apiclient, name, type(name, (APIException,), {}))

    apiclient.ASYNC_PAUSE = 'pause'
    apiclient.ASYNC_CONTINUE = 'continue'
    apiclient.Client = type('Client', (), {'__init__': lambda self, *a, **kw: None})

    client.apiclient = apiclient
    return client, apiclient


def _load_ci_base():
    """Import the functional suite's base.py without its dependencies.

    Everything mutated here is put back, because stestr runs the whole
    unit suite in one process and a stubbed shakenfist_client left in
    sys.modules would be a trap for a later test rather than a
    convenience for this one.
    """
    stub_names = ('prettytable', 'shakenfist_client',
                  'shakenfist_client.apiclient', 'shakenfist_ci',
                  'shakenfist_ci.base', 'shakenfist_ci.process',
                  'shakenfist_ci.retries')
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in stub_names}
    root_logger = logging.getLogger()
    saved_handlers = list(root_logger.handlers)
    saved_level = root_logger.level

    try:
        sys.path.insert(0, os.path.dirname(CI_SUITE))
        for name in stub_names:
            sys.modules.pop(name, None)

        prettytable = types.ModuleType('prettytable')
        prettytable.PrettyTable = type('PrettyTable', (), {})
        sys.modules['prettytable'] = prettytable

        client, apiclient = _client_stubs()
        sys.modules['shakenfist_client'] = client
        sys.modules['shakenfist_client.apiclient'] = apiclient

        import shakenfist_ci.base as ci_base
        return ci_base

    finally:
        sys.path[:] = saved_path
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)


ci_base = _load_ci_base()


class _PowerStateHarness(ci_base.BaseTestCase):
    """A bound ``self`` for ``_assert_power_state()`` without setUp().

    setUp() builds a real apiclient and writes a tracing event to
    /srv/ci/traces, neither of which a unit test should do, so the
    harness supplies only the one attribute the helper reads.
    """

    def __init__(self, system_client):
        # Deliberately not calling TestCase.__init__: this is not being
        # run as a test, only used as a bound self for the helper.
        self.system_client = system_client


class AssertPowerStateTestCase(test_base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.system_client = mock.MagicMock()
        self.harness = _PowerStateHarness(self.system_client)
        self.harness._emit_tracing_event = mock.MagicMock()
        self.harness._log_instance_events = mock.MagicMock()

    def test_matching_value_returns(self):
        self.system_client.get_instance.return_value = {'power_state': 'on'}

        self.harness._assert_power_state('uuid1', 'on', 'after create')

        self.system_client.get_instance.assert_called_once_with('uuid1')
        self.harness._log_instance_events.assert_not_called()
        self.harness._emit_tracing_event.assert_called_once()

    def test_wrong_value_fails_with_a_useful_message(self):
        self.system_client.get_instance.return_value = {'power_state': 'paused'}

        exc = self.assertRaises(
            AssertionError, self.harness._assert_power_state,
            'uuid1', 'on', 'after unpause')

        message = str(exc)
        self.assertIn("expected 'on'", message)
        self.assertIn("power_state 'paused'", message)
        self.assertIn('after unpause', message)
        self.harness._log_instance_events.assert_called_once_with('uuid1')

    def test_stale_on_when_off_expected_mentions_f13(self):
        self.system_client.get_instance.return_value = {'power_state': 'on'}

        exc = self.assertRaises(
            AssertionError, self.harness._assert_power_state,
            'uuid1', 'off', 'after power off')

        self.assertIn('F13', str(exc))

    def test_off_when_on_expected_does_not_mention_f13(self):
        self.system_client.get_instance.return_value = {'power_state': 'off'}

        exc = self.assertRaises(
            AssertionError, self.harness._assert_power_state,
            'uuid1', 'on', 'after power on')

        self.assertNotIn('F13', str(exc))

    def test_never_sleeps(self):
        self.system_client.get_instance.return_value = {'power_state': 'paused'}

        with mock.patch.object(ci_base.time, 'sleep') as mock_sleep:
            self.assertRaises(
                AssertionError, self.harness._assert_power_state,
                'uuid1', 'on', 'after unpause')

        mock_sleep.assert_not_called()

    def test_missing_power_state_key_fails(self):
        self.system_client.get_instance.return_value = {}

        self.assertRaises(
            AssertionError, self.harness._assert_power_state,
            'uuid1', 'on', 'after create')
