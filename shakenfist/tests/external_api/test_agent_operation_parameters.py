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
#
# The dependency edge parameters are here for the same reasons, plus one
# of their own: their refusal says the same thing however the edge is
# wrong, so that a caller cannot learn from it that an operation they may
# not read exists. That equality is asserted on the response bodies
# themselves rather than as three separate message assertions, because
# three assertions which happen to agree today are not the guarantee.

import copy
import json
import time
from unittest import mock
from uuid import uuid4

from shakenfist import baseobject
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.external_api import instance as api_instance
from shakenfist.external_api import validation
from shakenfist.instance import Instance
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.schema.object_types import ObjectType
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

        # Two real agent operations for an edge to name: one this
        # caller can read and one it cannot. These go through the real
        # AgentOperation because the validation helper looks them up in
        # the database, and _post() stubs out only the class the handler
        # creates with.
        self.dependency = self._agentop('foo')
        self.foreign = self._agentop('other')

        self.client = external_api.app.test_client()
        self.auth = self._auth('foo')
        self.system_auth = self._auth('system')

    def _agentop(self, namespace):
        return AgentOperation.new(
            str(uuid4()), namespace, self.instance_uuid,
            [{'command': 'execute', 'commandline': 'true'}])

    def _auth(self, namespace):
        resp = self.client.post('/auth', data=json.dumps(
            {'namespace': namespace, 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        return {'Authorization': 'Bearer %s' % resp.get_json()['access_token']}

    def _restore_node_uuid(self):
        config.NODE_UUID = self.saved_node_uuid

    def _post(self, body, path='agent/execute', auth=None):
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
                headers=auth or self.auth, data=json.dumps(body))
            self.after = time.time()
        return resp, agentop.new

    def assertDeadlineIsSecondsFromNow(self, new, seconds):
        """Assert the stored deadline is `seconds` from the request."""
        deadline = new.call_args.kwargs['deadline']
        self.assertGreaterEqual(deadline, self.before + seconds)
        self.assertLessEqual(deadline, self.after + seconds)

    def _execute(self, auth=None, **extra):
        body = {'command_line': 'id'}
        body.update(extra)
        return self._post(body, auth=auth)

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
        # No command this endpoint builds reports progress, so an
        # explicit 0.0 is the truthful record and NULL keeps
        # meaning "written by a pre-deadlines API node".
        _, new = self._execute()
        self.assertEqual(0.0, new.call_args.kwargs['progress_timeout'])

    def test_a_negative_deadline_is_refused_and_creates_nothing(self):
        resp, new = self._execute(deadline_seconds=-1)
        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertIn('deadline_seconds', resp.get_json()['error'])
        new.assert_not_called()

    def test_a_deadline_above_the_operator_ceiling_is_refused(self):
        # Issue #4074: the ceiling published as the parameter's maximum
        # is backed by a 400, like the minimum. With API validation
        # enforcing, that 400 comes from the published bound itself
        # rather than from the handler's guard, which is
        # the stronger version of the same property -- the two can no
        # longer disagree. The handler's guard remains and answers when
        # API_VALIDATION_MODE is not 'enforce'.
        resp, new = self._execute(
            deadline_seconds=config.AGENT_OPERATION_MAX_DEADLINE + 1)
        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertIn(
            'deadline_seconds: Must be greater than or equal to 0 and less '
            'than or equal to %d' % config.AGENT_OPERATION_MAX_DEADLINE,
            resp.get_json()['error'])
        new.assert_not_called()

    def test_the_ceiling_is_enforced_by_the_handler_in_warn_mode(self):
        # The rollback path: with validation warning rather than
        # enforcing, the handler's own guard is what refuses this, and
        # it names the configuration option an operator has to change.
        self.set_validation_mode('warn')

        resp, new = self._execute(
            deadline_seconds=config.AGENT_OPERATION_MAX_DEADLINE + 1)
        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertIn(
            'AGENT_OPERATION_MAX_DEADLINE', resp.get_json()['error'])
        new.assert_not_called()

    def test_an_unparsable_deadline_is_a_400_not_a_500(self):
        for value in ('soon', ['60'], {'seconds': 60}, True):
            with self.subTest(value=value):
                resp, new = self._execute(deadline_seconds=value)
                self.assertEqual(400, resp.status_code, resp.get_json())
                new.assert_not_called()

    def test_execute_does_not_accept_a_progress_timeout(self):
        # Execute runs commands which cannot report progress, so it has
        # no progress timeout; this pins that in code rather than in
        # prose. With API validation enforcing, an undeclared body key is
        # refused by name before the handler runs -- so this asserts the
        # parameter is genuinely absent, not merely undocumented, and
        # that the refusal says so in the API's own words rather than
        # the interpreter's.
        resp, new = self._execute(progress_timeout_seconds=30)
        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertEqual(
            'progress_timeout_seconds: not declared by this endpoint',
            resp.get_json()['error'])
        new.assert_not_called()

    def test_a_worker_without_the_declaration_refuses_by_name(self):
        """Issue #4076, accepted: a stale capability cache costs a clean
        refusal, never a silent mis-execution.

        The official client caches the capability list once per Client
        object, so during a rolling sf-api upgrade it can learn
        agentoperation-deadlines from an already-upgraded worker and
        then POST deadline_seconds to a worker which does not declare
        it. Such a worker is simulated by compiling this endpoint's
        declarations with the timing parameters removed. The acceptance
        recorded on API_CAPABILITIES in external_api/app.py rests on
        the refusal naming the parameter in the API's own words and
        creating nothing, so that is what this pins.
        """
        specs = copy.deepcopy(
            api_instance.InstanceAgentExecuteEndpoint.post.specs_dict)
        for parameter in specs['parameters']:
            schema = parameter.get('schema')
            if schema and schema.get('type') == 'object':
                for name in ('deadline_seconds', 'progress_timeout_seconds'):
                    schema['properties'].pop(name, None)
        stale = validation.compile_parameters(specs['parameters'])
        self.assertNotIn('deadline_seconds', stale.names)

        with mock.patch.dict(
                validation.REGISTRY,
                {('InstanceAgentExecuteEndpoint', 'post'): stale}):
            resp, new = self._execute(deadline_seconds=30)

        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertEqual(
            {'error': 'deadline_seconds: not declared by this endpoint',
             'status': 400},
            resp.get_json())
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

    def test_put_of_a_missing_blob_is_a_404_not_a_500(self):
        # Issue #4194: this arm used to call self.api_error, a method
        # which does not exist, so a well-formed uuid naming no blob
        # raised AttributeError and answered an opaque 500. The uuid is
        # valid and passes every schema check; Blob.from_db returning
        # None is exactly what a deleted blob, a typo or a uuid from
        # another cluster produces.
        with mock.patch('shakenfist.external_api.instance.Blob.from_db',
                        return_value=None):
            resp, new = self._post(
                {'blob_uuid': str(uuid4()), 'path': '/tmp/README.md',
                 'mode': '33188'},
                path='agent/put')
        self.assertEqual(404, resp.status_code, resp.get_json())
        self.assertEqual(
            {'error': 'blob not found', 'status': 404}, resp.get_json())
        new.assert_not_called()

    def test_put_accepts_both_parameters(self):
        resp, new = self._put(deadline_seconds=45,
                              progress_timeout_seconds=15)
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertDeadlineIsSecondsFromNow(new, 45)
        self.assertEqual(15.0, new.call_args.kwargs['progress_timeout'])

    def test_put_preflight_targets_the_instance(self):
        # The preflight is a cluster operation targeting the agent
        # operation and the instance it runs on, so that deleting the
        # instance cancels it (issue 4672). The mock database runs the
        # real enqueue path, so the rows are what sf-database would hold.
        resp, new = self._put()
        self.assertEqual(200, resp.status_code, resp.get_json())

        preflights = [
            op_uuid for op_uuid, row in
            self.mock_mariadb.cluster_operations_store.items()
            if row['operation_type'] == 'node_aop_op']
        self.assertEqual(1, len(preflights))
        self.assertEqual(
            [('agentoperation', new.return_value.uuid),
             ('instance', self.instance_uuid)],
            [(t.target_object_type, t.target_uuid)
             for t in self.mock_mariadb.get_cluster_operation_targets(
                 preflights[0])])

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

    # ------------------------------------------------------------------
    # Dependency edges.
    #
    # Nothing evaluates an edge yet, so what these pin is the API
    # contract: what is stored, what is refused, and -- the part a later
    # reader is most likely to "tidy" -- that every refusal says the same
    # thing.

    def _edge(self, operation, **extra):
        edge = {'op_uuid': str(operation.uuid)}
        edge.update(extra)
        return edge

    def test_an_edge_is_stored_with_its_op_type_filled_in(self):
        # The caller sends no op_type, because only one value is legal
        # for it. The stored edge carries one anyway, so that a persisted
        # agent edge is the same shape as a persisted cluster one -- and
        # it is the helper which fills it in, so the handler never writes
        # the dictionary the caller sent.
        resp, new = self._execute(depends_on=[self._edge(self.dependency)])
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(
            [{'op_type': 'agentoperation',
              'op_uuid': str(self.dependency.uuid),
              'settle_seconds': None}],
            new.call_args.kwargs['depends_on'])
        self.assertEqual([], new.call_args.kwargs['runs_after'])

    def test_omitting_both_lists_stores_two_empty_lists(self):
        # Empty lists rather than None at the API layer too: the columns
        # are not nullable, and a client which read back null would have
        # to guess whether it meant "declares nothing" or "this server
        # does not know about edges".
        resp, new = self._execute()
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual([], new.call_args.kwargs['depends_on'])
        self.assertEqual([], new.call_args.kwargs['runs_after'])

    def test_a_settle_delay_reaches_the_object(self):
        resp, new = self._execute(
            depends_on=[self._edge(self.dependency, settle_seconds=30)])
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(
            30.0, new.call_args.kwargs['depends_on'][0]['settle_seconds'])

    def test_an_edge_naming_nothing_is_refused_and_creates_nothing(self):
        # The refusal happens where the timing refusal happens, before
        # any object or event exists, so a request which named a bad
        # dependency leaves nothing behind but the request log.
        resp, new = self._execute(
            depends_on=[{'op_uuid': str(uuid4())}])
        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertEqual(
            {'error': 'depends_on[0]: no such agent operation', 'status': 400},
            resp.get_json())
        new.assert_not_called()

    def test_every_refusal_reason_answers_the_same_bytes(self):
        """The non-disclosure guarantee, and the only test of it.

        An edge is refused for three reasons: the operation does not
        exist, it is not an agent operation, and it is in a namespace this
        caller cannot read. If the three answered differently, a caller
        could submit edges to discover which uuids exist in namespaces
        they cannot see, which is what the 400-rather-than-404 posture
        here would otherwise cost. The bodies are compared to each other,
        not to three copies of one expected string, because the equality
        is the property.
        """
        missing, missing_new = self._execute(
            depends_on=[{'op_uuid': str(uuid4())}])
        self.assertEqual(400, missing.status_code, missing.get_json())
        missing_new.assert_not_called()

        other_namespace, other_new = self._execute(
            depends_on=[self._edge(self.foreign)])
        self.assertEqual(
            400, other_namespace.status_code, other_namespace.get_json())
        other_new.assert_not_called()

        # The third reason, which cannot arise through the real lookup --
        # from_db() on this class reads the agent operation table and so
        # builds nothing else. It is reachable only by making the lookup
        # answer with something of another type, which is exactly what a
        # future widening of it would do.
        not_an_operation = mock.MagicMock()
        not_an_operation.object_type = ObjectType.INSTANCE
        not_an_operation.namespace = 'foo'
        with mock.patch.object(api_base.AgentOperation, 'from_db',
                               return_value=not_an_operation):
            wrong_type, wrong_type_new = self._execute(
                depends_on=[self._edge(self.dependency)])
        self.assertEqual(400, wrong_type.status_code, wrong_type.get_json())
        wrong_type_new.assert_not_called()

        self.assertEqual(missing.get_json(), other_namespace.get_json())
        self.assertEqual(missing.get_json(), wrong_type.get_json())
        self.assertEqual(
            {'error': 'depends_on[0]: no such agent operation', 'status': 400},
            missing.get_json())

    def test_a_system_caller_may_name_another_namespaces_operation(self):
        # The other half of the namespace test, and the half an inverted
        # check breaks instead: the test admits a caller in the
        # dependency's own namespace *and* a system caller, so inverting
        # it either lets any caller name any operation -- which the
        # cross-namespace refusal above would catch -- or locks system
        # out, which only this catches.
        resp, new = self._execute(
            auth=self.system_auth,
            depends_on=[self._edge(self.foreign)])
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(
            [{'op_type': 'agentoperation', 'op_uuid': str(self.foreign.uuid),
              'settle_seconds': None}],
            new.call_args.kwargs['depends_on'])

    def test_a_caller_may_not_publish_an_op_type(self):
        # additionalProperties: False on the edge object, which is what
        # keeps op_type out of the published vocabulary rather than merely
        # undocumented. A caller who sends it is told so.
        resp, new = self._execute(
            depends_on=[self._edge(self.dependency,
                                   op_type='clusteroperation')])
        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertEqual(
            {'error': 'depends_on[0].op_type: Unknown field.', 'status': 400},
            resp.get_json())
        new.assert_not_called()

    def test_a_settle_delay_above_the_operator_ceiling_is_refused(self):
        # The same ceiling the timing parameters publish, and for the same
        # reason: a settle parks the instance's one executor slot while it
        # runs.
        resp, new = self._execute(
            depends_on=[self._edge(
                self.dependency,
                settle_seconds=config.AGENT_OPERATION_MAX_DEADLINE + 1)])
        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertIn('settle_seconds', resp.get_json()['error'])
        new.assert_not_called()

    def test_a_negative_settle_delay_is_refused(self):
        resp, new = self._execute(
            depends_on=[self._edge(self.dependency, settle_seconds=-1)])
        self.assertEqual(400, resp.status_code, resp.get_json())
        self.assertIn('settle_seconds', resp.get_json()['error'])
        new.assert_not_called()

    def test_both_parameters_are_declared_on_every_endpoint(self):
        # Unlike progress_timeout_seconds there is no agent operation for
        # which an edge is meaningless, so all three endpoints take both.
        # The three handlers call the helper separately with near
        # identical code, so a copy which forgot to pass its result on
        # would be invisible to a test which drove only one route.
        bodies = {
            'agent/execute': {'command_line': 'id'},
            'agent/get': {'path': '/etc/hostname'},
            'agent/put': {'blob_uuid': str(uuid4()),
                          'path': '/tmp/README.md', 'mode': '33188'},
        }
        for (path, body) in bodies.items():
            for parameter in ('depends_on', 'runs_after'):
                with self.subTest(path=path, parameter=parameter):
                    sent = dict(body)
                    sent[parameter] = [self._edge(self.dependency)]
                    with mock.patch(
                            'shakenfist.external_api.instance.Blob.from_db',
                            return_value=mock.MagicMock()):
                        resp, new = self._post(sent, path=path)
                    self.assertEqual(200, resp.status_code, resp.get_json())
                    self.assertEqual(
                        [{'op_type': 'agentoperation',
                          'op_uuid': str(self.dependency.uuid),
                          'settle_seconds': None}],
                        new.call_args.kwargs[parameter])

    def test_every_endpoint_refuses_a_bad_edge(self):
        # The refusal half of the sweep above. A handler which called the
        # helper and ignored its error would pass every test here but this
        # one.
        bodies = {
            'agent/execute': {'command_line': 'id'},
            'agent/get': {'path': '/etc/hostname'},
            'agent/put': {'blob_uuid': str(uuid4()),
                          'path': '/tmp/README.md', 'mode': '33188'},
        }
        for (path, body) in bodies.items():
            for parameter in ('depends_on', 'runs_after'):
                with self.subTest(path=path, parameter=parameter):
                    sent = dict(body)
                    sent[parameter] = [{'op_uuid': str(uuid4())}]
                    with mock.patch(
                            'shakenfist.external_api.instance.Blob.from_db',
                            return_value=mock.MagicMock()):
                        resp, new = self._post(sent, path=path)
                    self.assertEqual(400, resp.status_code, resp.get_json())
                    self.assertEqual(
                        {'error': '%s[0]: no such agent operation' % parameter,
                         'status': 400},
                        resp.get_json())
                    new.assert_not_called()
