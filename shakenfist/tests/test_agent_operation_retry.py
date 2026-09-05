# Copyright 2019 Michael Still and contributors

import uuid
from unittest import mock

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import SFConfig
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.schema.agentoperation_data import AgentOperationData
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class AgentOperationRetryTestCase(base.ShakenFistTestCase):
    """The executing -> queued retry edge, and the attempt/results writers."""

    def setUp(self):
        super().setUp()
        fake_config = SFConfig(
            STORAGE_PATH='/a/b/c',
            DISK_BUS='virtio',
            ZONE='sfzone',
            NODE_NAME='node01',
        )

        self.config = mock.patch(
            'shakenfist.operations.agentoperation.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.gmov = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version', return_value=6)
        self.mock_gmov = self.gmov.start()
        self.addCleanup(self.gmov.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.instance_uuid = str(uuid.uuid4())
        self.mock_mariadb.create_instance('cirros', self.instance_uuid)

    # The state machine is enforced, so reaching a state means walking
    # to it rather than assigning it.
    _PATHS = {
        dbo.STATE_INITIAL: [],
        AgentOperation.STATE_PREFLIGHT: [AgentOperation.STATE_PREFLIGHT],
        AgentOperation.STATE_QUEUED: [AgentOperation.STATE_QUEUED],
        AgentOperation.STATE_EXECUTING: [AgentOperation.STATE_QUEUED,
                                         AgentOperation.STATE_EXECUTING],
        AgentOperation.STATE_COMPLETE: [AgentOperation.STATE_QUEUED,
                                        AgentOperation.STATE_EXECUTING,
                                        AgentOperation.STATE_COMPLETE],
    }

    def _make_agentop(self, state=None):
        op = AgentOperation.new(
            str(uuid.uuid4()), 'unittest', self.instance_uuid,
            [{'command': 'execute', 'commandline': 'true'}])
        for step in self._PATHS[state or dbo.STATE_INITIAL]:
            op.state = step
        return op

    # The retry edge itself.
    def test_executing_to_queued_is_permitted(self):
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.state = AgentOperation.STATE_QUEUED
        self.assertEqual(AgentOperation.STATE_QUEUED, op.state.value)

    def test_expired_to_queued_is_not_permitted(self):
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.expire('deadline passed', AgentOperation.EXPIRY_REASON_DEADLINE)
        self.assertRaises(
            exceptions.InvalidStateException,
            setattr, op, 'state', AgentOperation.STATE_QUEUED)

    def test_complete_to_queued_is_not_permitted(self):
        op = self._make_agentop(state=AgentOperation.STATE_COMPLETE)
        self.assertRaises(
            exceptions.InvalidStateException,
            setattr, op, 'state', AgentOperation.STATE_QUEUED)

    # record_attempt()
    def test_record_attempt_increments_from_the_default(self):
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        self.assertEqual(0, op.attempts)

        op.record_attempt()
        self.assertEqual(1, op.attempts)

        op.record_attempt()
        self.assertEqual(2, op.attempts)

    def test_record_attempt_writes_a_masked_field(self):
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        with mock.patch(
                'shakenfist.operations.agentoperation.mariadb'
                '.update_agent_operation_attributes') as mock_update:
            op.record_attempt()

        mock_update.assert_called_once()
        self.assertEqual(
            ['attempts'], mock_update.call_args.kwargs['fields'])
        self.assertEqual(1, mock_update.call_args.args[0].attempts)

    # clear_results()
    def test_clear_results_empties_the_dict(self):
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.add_result(0, {'stdout': 'hello'})
        self.assertEqual({'0': {'stdout': 'hello'}}, op.results)

        op.clear_results()
        self.assertEqual({}, op.results)

    def test_clear_results_writes_a_masked_field(self):
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.add_result(0, {'stdout': 'hello'})

        with mock.patch(
                'shakenfist.operations.agentoperation.mariadb'
                '.update_agent_operation_attributes') as mock_update:
            op.clear_results()

        mock_update.assert_called_once()
        self.assertEqual(
            ['results'], mock_update.call_args.kwargs['fields'])
        self.assertEqual({}, mock_update.call_args.args[0].results)

    def test_clear_results_emits_an_event(self):
        # This is the only place results are destroyed rather than
        # overwritten. Without an event the operation's stream shows
        # results appearing and then simply not being there, with
        # nothing tying the disappearance to the retry which caused
        # it -- and the project's stated goal is to keep event
        # coverage good enough to audit from.
        op = self._make_agentop(state=AgentOperation.STATE_EXECUTING)
        op.add_result(0, {'stdout': 'hello'})
        op.add_result(1, {'stdout': 'world'})

        with mock.patch.object(op, 'add_event') as mock_event:
            op.clear_results()

        mock_event.assert_called_once()
        self.assertEqual('clear results', mock_event.call_args.args[1])
        self.assertEqual(2, mock_event.call_args.kwargs['extra']['cleared'])


class AgentOperationCommandListTestCase(base.ShakenFistTestCase):
    """_db_get() must not hand out the cached model's own command list.

    mariadb.get_agent_operation() caches the AgentOperationData model
    for OBJECT_CACHE_TTL_IMMUTABLE seconds, and pydantic's frozen=True
    stops attribute assignment rather than mutation of a list field's
    contents. While _db_get() referenced data.commands, every
    AgentOperation built from a cache hit shared one list with the
    cache and with every other such object, so a single mutation
    anywhere on the node was visible everywhere until the entry aged
    out.
    """

    def _data(self):
        return AgentOperationData(
            uuid=uuid.uuid4(), namespace='ns', instance_uuid=uuid.uuid4(),
            commands=[{'command': 'get-file', 'path': '/tmp/x'}],
            deadline=None, progress_timeout=None,
            version=AgentOperation.current_version)

    def test_the_static_values_do_not_alias_the_cached_model(self):
        data = self._data()
        with mock.patch('shakenfist.operations.agentoperation.mariadb.'
                        'get_agent_operation', return_value=data):
            first = AgentOperation._db_get(str(data.uuid))
            second = AgentOperation._db_get(str(data.uuid))

        self.assertIsNot(data.commands, first['commands'])
        self.assertIsNot(first['commands'], second['commands'])

    def test_mutating_one_reader_leaves_the_others_alone(self):
        data = self._data()
        with mock.patch('shakenfist.operations.agentoperation.mariadb.'
                        'get_agent_operation', return_value=data):
            first = AgentOperation._db_get(str(data.uuid))
            second = AgentOperation._db_get(str(data.uuid))

        first['commands'].pop(0)

        expected = [{'command': 'get-file', 'path': '/tmp/x'}]
        self.assertEqual(expected, data.commands)
        self.assertEqual(expected, second['commands'])

    def test_two_objects_from_one_cache_entry_do_not_share_a_list(self):
        # The two above pin the mechanism at the dict _db_get() returns.
        # This pins the behaviour readers actually depend on: every
        # AgentOperation built while one cache entry is live owns its
        # own command list. Without it a reader could be reverted onto
        # shared static values as an allocation saving and the tests
        # above would still pass.
        data = self._data()
        with mock.patch('shakenfist.operations.agentoperation.mariadb.'
                        'get_agent_operation', return_value=data):
            first = AgentOperation.from_db(str(data.uuid))
            second = AgentOperation.from_db(str(data.uuid))

        self.assertIsNot(first.commands, second.commands)
        self.assertIsNot(data.commands, first.commands)

        # The second consequence the fix comment names: a later reader
        # on the node still sees the put-blob command it has to make
        # local before dispatch. NodeAgentopOp._preflight iterates
        # exactly this list.
        first.commands.pop(0)
        self.assertEqual(
            [{'command': 'get-file', 'path': '/tmp/x'}], second.commands)
        self.assertEqual(
            [{'command': 'get-file', 'path': '/tmp/x'}], data.commands)
