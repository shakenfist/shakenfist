# Copyright 2019 Michael Still and contributors

import uuid
from unittest import mock

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import SFConfig
from shakenfist.operations.agentoperation import AgentOperation
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
        op.expire('deadline passed')
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
