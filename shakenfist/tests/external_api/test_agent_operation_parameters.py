# Copyright 2019 Michael Still and contributors
#
# End to end tests for the agent operation timing parameters, driving
# real requests at /instances/<ref>/agent/execute.
#
# test_agent_operation_timing.py covers the conversion helper directly.
# What this adds is that the parameters are wired: that they are
# declared (an undeclared body key is a 400 from the kwargs merge, not
# a silently ignored value), that a refusal happens before anything is
# created, and that what reaches AgentOperation.new() is the absolute
# timestamp the enforcement phase will read rather than the relative
# seconds the caller sent.

import json
import time
from unittest import mock
from uuid import uuid4

from shakenfist import baseobject
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.instance import Instance
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class AgentOperationParametersTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        # Placed on this node so redirect_instance_request() runs the
        # handler here rather than proxying it, the same reason
        # test_snapshot_max_versions.py does this.
        self.saved_node_uuid = config.NODE_UUID
        config.NODE_UUID = self.mock_mariadb.node_uuids['node1_net']
        self.addCleanup(self._restore_node_uuid)

        self.instance_uuid = str(uuid4())
        self.mock_mariadb.create_instance(
            'agentme', uuid=self.instance_uuid, namespace='foo',
            set_state=dbo.STATE_CREATED, place_on_node=config.NODE_UUID)

        # The three endpoints refuse an instance whose agent is not
        # ready before they look at anything else.
        agent_state = mock.patch.object(
            Instance, 'agent_state',
            new_callable=mock.PropertyMock,
            return_value=baseobject.State(
                value='ready', update_time=time.time()))
        agent_state.start()
        self.addCleanup(agent_state.stop)

        self.client = external_api.app.test_client()
        resp = self.client.post('/auth', data=json.dumps(
            {'namespace': 'foo', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth = {
            'Authorization': 'Bearer %s' % resp.get_json()['access_token']}

    def _restore_node_uuid(self):
        config.NODE_UUID = self.saved_node_uuid

    def _post(self, body, path='agent/execute'):
        # The clock is deliberately not frozen here. base.py does
        # "import time", so patching base.time.time patches the
        # attribute on the shared module object and every module in the
        # process -- event logging, locks, the auth layer -- sees the
        # fake time for the duration of a real Flask request. Instead
        # the request is bracketed and the deadline assertions are
        # ranges; the exact-value assertions live in
        # test_agent_operation_timing.py, which calls the helper
        # directly and can freeze time locally and safely.
        #
        # AgentOperation.new() is stubbed because what it was called
        # with is the question, and because the real one would enqueue
        # work this harness has no executor for.
        with mock.patch('shakenfist.external_api.instance.AgentOperation',
                        autospec=True) as agentop:
            agentop.new.return_value.external_view.return_value = {}
            # put enqueues a preflight task naming this uuid, and the
            # task's pydantic model will not take a MagicMock.
            agentop.new.return_value.uuid = str(uuid4())
            agentop.STATE_QUEUED = 'queued'
            agentop.STATE_PREFLIGHT = 'preflight'
            self.before = time.time()
            resp = self.client.post(
                '/instances/%s/%s' % (self.instance_uuid, path),
                headers=self.auth, data=json.dumps(body))
            self.after = time.time()
        return resp, agentop.new

    def assertDeadlineIsSecondsFromNow(self, new, seconds):
        """Assert the stored deadline is `seconds` from the request."""
        deadline = new.call_args.kwargs['deadline']
        self.assertGreaterEqual(deadline, self.before + seconds)
        self.assertLessEqual(deadline, self.after + seconds)

    def _execute(self, **extra):
        body = {'command_line': 'id'}
        body.update(extra)
        return self._post(body)

    def test_omitted_stores_the_default_deadline(self):
        resp, new = self._execute()
        self.assertEqual(200, resp.status_code, resp.get_json())
        new.assert_called_once()
        self.assertDeadlineIsSecondsFromNow(
            new, config.AGENT_OPERATION_DEFAULT_DEADLINE)

    def test_omitted_does_not_store_null(self):
        # The tempting alternative -- write NULL and let the
        # enforcement phase apply the default -- would move the
        # deadline's anchor from request receipt to dispatch, and would
        # make every new row indistinguishable from one written by an
        # API server which predates deadlines.
        _, new = self._execute()
        self.assertIsNotNone(new.call_args.kwargs['deadline'])

    def test_a_value_arrives_as_an_absolute_timestamp(self):
        resp, new = self._execute(deadline_seconds=90)
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertDeadlineIsSecondsFromNow(new, 90)

    def test_an_explicit_zero_arrives_as_zero(self):
        resp, new = self._execute(deadline_seconds=0)
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(0.0, new.call_args.kwargs['deadline'])

    def test_execute_records_no_progress_timeout(self):
        # Decision 4: no command this endpoint builds reports progress,
        # so an explicit 0.0 is the truthful record and NULL keeps
        # meaning "written by a pre-deadlines API node".
        _, new = self._execute()
        self.assertEqual(0.0, new.call_args.kwargs['progress_timeout'])

    def test_a_negative_deadline_is_refused_and_creates_nothing(self):
        resp, new = self._execute(deadline_seconds=-1)
        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertIn('deadline_seconds', resp.get_json()['error'])
        new.assert_not_called()

    def test_an_unparsable_deadline_is_a_400_not_a_500(self):
        for value in ('soon', ['60'], {'seconds': 60}, True):
            with self.subTest(value=value):
                resp, new = self._execute(deadline_seconds=value)
                self.assertEqual(400, resp.status_code, resp.get_json())
                new.assert_not_called()

    def test_execute_does_not_accept_a_progress_timeout(self):
        # Pins decision 4 in code rather than in prose. An undeclared
        # body key becomes an unexpected keyword argument in the kwargs
        # merge, which is a 400 -- so this asserts the parameter is
        # genuinely absent, not merely undocumented.
        resp, new = self._execute(progress_timeout_seconds=30)
        self.assertEqual(400, resp.status_code, resp.get_json())
        new.assert_not_called()

    def test_get_accepts_both_parameters(self):
        # The control for the assertion above: the same body key is
        # accepted where a command really can report progress.
        resp, new = self._post(
            {'path': '/etc/hostname', 'deadline_seconds': 30,
             'progress_timeout_seconds': 5},
            path='agent/get')
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertDeadlineIsSecondsFromNow(new, 30)
        self.assertEqual(5.0, new.call_args.kwargs['progress_timeout'])

    def test_get_refuses_a_negative_progress_timeout(self):
        # Each endpoint gets its own refusal test rather than trusting
        # the execute one to cover them: the three call the helper
        # separately, and a copy which logged the error and carried on
        # would be invisible to a test that only drives one route.
        resp, new = self._post(
            {'path': '/etc/hostname', 'progress_timeout_seconds': -1},
            path='agent/get')
        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertIn('progress_timeout_seconds', resp.get_json()['error'])
        new.assert_not_called()

    def test_put_refuses_a_negative_deadline_before_looking_up_the_blob(self):
        # The timing check precedes the mode parse and the blob lookup,
        # so this needs neither to be valid. That ordering is the point:
        # a refused request must not have gone looking for anything.
        resp, new = self._post(
            {'blob_uuid': str(uuid4()), 'path': '/tmp/x', 'mode': 'nonsense',
             'deadline_seconds': -1},
            path='agent/put')
        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertIn('deadline_seconds', resp.get_json()['error'])
        new.assert_not_called()

    def _put(self, **extra):
        # Unlike execute and get, put reaches a blob lookup on its way
        # to AgentOperation.new(), so the success path needs one to
        # exist. mode is numeric to skip the symbolic parse.
        body = {'blob_uuid': str(uuid4()), 'path': '/tmp/README.md',
                'mode': '33188'}
        body.update(extra)
        with mock.patch('shakenfist.external_api.instance.Blob.from_db',
                        return_value=mock.MagicMock()):
            return self._post(body, path='agent/put')

    def test_put_accepts_both_parameters(self):
        resp, new = self._put(deadline_seconds=45,
                              progress_timeout_seconds=15)
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertDeadlineIsSecondsFromNow(new, 45)
        self.assertEqual(15.0, new.call_args.kwargs['progress_timeout'])

    def test_put_applies_the_progress_default_when_omitted(self):
        # This is what pins put's progress_capable argument. The three
        # handlers call the helper separately with near-identical code,
        # so a False copied across from execute would silently give the
        # one endpoint whose transfer most needs a progress window no
        # progress window at all, and every other test here would still
        # pass.
        resp, new = self._put()
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(
            float(config.AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT),
            new.call_args.kwargs['progress_timeout'])
        self.assertNotEqual(0.0, new.call_args.kwargs['progress_timeout'])

    def test_put_applies_the_default_deadline_when_omitted(self):
        resp, new = self._put()
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertDeadlineIsSecondsFromNow(
            new, config.AGENT_OPERATION_DEFAULT_DEADLINE)

    def test_get_applies_the_progress_default_when_omitted(self):
        resp, new = self._post({'path': '/etc/hostname'}, path='agent/get')
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(
            float(config.AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT),
            new.call_args.kwargs['progress_timeout'])
