# Copyright 2019 Michael Still and contributors
#
# Unit tests for the agent operation deadline and progress fields as
# they cross the gRPC boundary.
#
# These four fields carry a three-valued meaning which proto3 can only
# express with explicit field presence: absent means SQL NULL ("no
# client intent was recorded, so the server default applies"), while an
# explicit 0.0 means the client asked for none. Every daemon except
# sf-database itself reaches MariaDB across this boundary, so a
# converter which collapsed those two cases would leave the direct path
# correct and the path everything actually uses wrong.

from unittest import mock
from uuid import UUID

from shakenfist import mariadb
from shakenfist.daemons.database.main import DatabaseService
from shakenfist.protos import database_pb2
from shakenfist.schema.agentoperation_attributes import (
    AgentOperationAttributesData)
from shakenfist.schema.agentoperation_data import AgentOperationData
from shakenfist.tests import base


AOP_UUID = UUID('aaaabbbb-0000-4000-8000-00000000000a')
INSTANCE_UUID = UUID('aaaabbbb-0000-4000-8000-00000000000b')


def _static(deadline, progress_timeout):
    return AgentOperationData(
        uuid=AOP_UUID,
        namespace='banana',
        instance_uuid=INSTANCE_UUID,
        commands=[{'command': 'execute'}],
        deadline=deadline,
        progress_timeout=progress_timeout,
        version=3)


class AgentOperationProtoRoundTripTestCase(base.ShakenFistTestCase):
    """The converters must preserve None, 0.0 and a real value."""

    def setUp(self):
        super().setUp()
        # The converters are pure functions of their arguments, so an
        # unconstructed servicer is enough and avoids standing up a
        # gRPC server for a data conversion test.
        self.service = DatabaseService.__new__(DatabaseService)

    def _round_trip_static(self, deadline, progress_timeout):
        data = _static(deadline, progress_timeout)
        proto = self.service._agentop_to_proto(data)
        return proto, self.service._agentop_from_proto(proto)

    def test_static_none_stays_none(self):
        proto, out = self._round_trip_static(None, None)
        self.assertFalse(proto.HasField('deadline'))
        self.assertFalse(proto.HasField('progress_timeout'))
        self.assertIsNone(out.deadline)
        self.assertIsNone(out.progress_timeout)

    def test_static_zero_is_not_none(self):
        # This is the assertion the whole "optional" keyword exists
        # for. Without field presence both of these would come back
        # as None and the client's explicit "no deadline" would be
        # indistinguishable from a legacy row.
        proto, out = self._round_trip_static(0.0, 0.0)
        self.assertTrue(proto.HasField('deadline'))
        self.assertTrue(proto.HasField('progress_timeout'))
        self.assertEqual(0.0, out.deadline)
        self.assertEqual(0.0, out.progress_timeout)

    def test_static_values_survive(self):
        _, out = self._round_trip_static(1787427490.5, 30.0)
        self.assertEqual(1787427490.5, out.deadline)
        self.assertEqual(30.0, out.progress_timeout)

    def test_static_mixed_none_and_zero(self):
        proto, out = self._round_trip_static(None, 0.0)
        self.assertFalse(proto.HasField('deadline'))
        self.assertTrue(proto.HasField('progress_timeout'))
        self.assertIsNone(out.deadline)
        self.assertEqual(0.0, out.progress_timeout)

    def _round_trip_attrs(self, last_progress, attempts,
                          expiry_reason=None):
        data = AgentOperationAttributesData(
            uuid=AOP_UUID,
            results={'0': {'status': 0}},
            last_progress=last_progress,
            attempts=attempts,
            expiry_reason=expiry_reason)
        proto = self.service._agentop_attrs_to_proto(data)
        return proto, self.service._agentop_attrs_from_proto(proto)

    def test_attributes_none_stays_none(self):
        proto, out = self._round_trip_attrs(None, 0)
        self.assertFalse(proto.HasField('last_progress'))
        self.assertFalse(proto.HasField('expiry_reason'))
        self.assertIsNone(out.last_progress)
        self.assertEqual(0, out.attempts)
        self.assertIsNone(out.expiry_reason)
        self.assertEqual({'0': {'status': 0}}, out.results)

    def test_attributes_zero_progress_is_not_none(self):
        proto, out = self._round_trip_attrs(0.0, 3)
        self.assertTrue(proto.HasField('last_progress'))
        self.assertEqual(0.0, out.last_progress)
        self.assertEqual(3, out.attempts)

    def test_attributes_values_survive(self):
        _, out = self._round_trip_attrs(1787427490.5, 2,
                                        expiry_reason='deadline')
        self.assertEqual(1787427490.5, out.last_progress)
        self.assertEqual(2, out.attempts)
        self.assertEqual('deadline', out.expiry_reason)


class AgentOperationProtoDefaultsTestCase(base.ShakenFistTestCase):
    """An old peer sends no new fields at all."""

    def setUp(self):
        super().setUp()
        self.service = DatabaseService.__new__(DatabaseService)

    def test_static_message_without_new_fields(self):
        # What a not-yet-upgraded client's create request looks like
        # on the wire. Both new values must read as "no client intent
        # recorded" rather than as an explicit zero, because the
        # server default is the safe reading and an explicit zero
        # would mean "never time this out".
        proto = database_pb2.AgentOperationStaticData(
            uuid=str(AOP_UUID),
            namespace='banana',
            instance_uuid=str(INSTANCE_UUID),
            commands_json='[]',
            version=3)
        out = self.service._agentop_from_proto(proto)
        self.assertIsNone(out.deadline)
        self.assertIsNone(out.progress_timeout)

    def test_attributes_message_without_new_fields(self):
        proto = database_pb2.AgentOperationAttributesProto(
            uuid=str(AOP_UUID),
            results_json='{}')
        out = self.service._agentop_attrs_from_proto(proto)
        self.assertIsNone(out.last_progress)
        self.assertEqual(0, out.attempts)
        self.assertIsNone(out.expiry_reason)


class AgentOperationGrpcClientTestCase(base.ShakenFistTestCase):
    """The client half of the boundary, which every daemon but sf-database uses.

    The converters above are the server half. These tests drive the
    request protos mariadb.py actually builds and then hand them to the
    servicer, so a client which set presence differently from
    _agentop_to_proto would be caught rather than papered over by
    testing only one side of the same wire.
    """

    def setUp(self):
        super().setUp()
        self.service = DatabaseService.__new__(DatabaseService)
        # The servicer entry point bumps a prometheus counter before
        # doing anything, and an unconstructed servicer has no monitor.
        self.service.monitor = mock.MagicMock()
        self.requests = []
        self.reply = mock.MagicMock(success=True)

        stub = mock.patch('shakenfist.mariadb._get_database_stub')
        stub.start()
        self.addCleanup(stub.stop)

        def _capture(method, request, *args, **kwargs):
            self.requests.append(request)
            return self.reply

        call = mock.patch('shakenfist.mariadb._grpc_call',
                          side_effect=_capture)
        call.start()
        self.addCleanup(call.stop)

    def test_create_request_leaves_none_unset(self):
        self.assertTrue(
            mariadb._grpc_create_agent_operation(_static(None, None)))
        data = self.requests[0].data
        self.assertFalse(data.HasField('deadline'))
        self.assertFalse(data.HasField('progress_timeout'))
        # And the servicer reads that back as "no client intent".
        out = self.service._agentop_from_proto(data)
        self.assertIsNone(out.deadline)
        self.assertIsNone(out.progress_timeout)

    def test_create_request_sets_an_explicit_zero(self):
        self.assertTrue(
            mariadb._grpc_create_agent_operation(_static(0.0, 0.0)))
        data = self.requests[0].data
        self.assertTrue(data.HasField('deadline'))
        self.assertTrue(data.HasField('progress_timeout'))
        out = self.service._agentop_from_proto(data)
        self.assertEqual(0.0, out.deadline)
        self.assertEqual(0.0, out.progress_timeout)

    def test_get_reply_distinguishes_unset_from_zero(self):
        self.reply = mock.MagicMock(
            found=True,
            data=self.service._agentop_to_proto(_static(None, 0.0)))
        out = mariadb._grpc_get_agent_operation(AOP_UUID)
        self.assertIsNone(out.deadline)
        self.assertEqual(0.0, out.progress_timeout)

    def test_attributes_create_request_leaves_none_unset(self):
        data = AgentOperationAttributesData(
            uuid=AOP_UUID, results={}, last_progress=None, attempts=0)
        self.assertTrue(
            mariadb._grpc_create_agent_operation_attributes(data))
        proto = self.requests[0].data
        self.assertFalse(proto.HasField('last_progress'))
        self.assertFalse(proto.HasField('expiry_reason'))
        self.assertEqual(0, proto.attempts)

    def test_attributes_get_reply_distinguishes_unset_from_zero(self):
        stored = AgentOperationAttributesData(
            uuid=AOP_UUID, results={}, last_progress=0.0, attempts=4,
            expiry_reason='progress')
        self.reply = mock.MagicMock(
            found=True,
            data=self.service._agentop_attrs_to_proto(stored))
        out = mariadb._grpc_get_agent_operation_attributes(AOP_UUID)
        self.assertEqual(0.0, out.last_progress)
        self.assertEqual(4, out.attempts)
        self.assertEqual('progress', out.expiry_reason)

    def test_update_mask_survives_the_round_trip_to_the_direct_layer(self):
        # The direct path's mask is covered by the live suite and by
        # test_mariadb_instance_attributes.py, but nothing asserted the
        # mask actually crossed the wire. Without that, a client which
        # dropped `fields` would write every column -- the
        # cross-attribute lost update CLAUDE.md warns about -- on the
        # path every daemon except sf-database uses.
        data = AgentOperationAttributesData(
            uuid=AOP_UUID, results={'0': {'status': 0}},
            last_progress=None, attempts=0)
        self.assertTrue(
            mariadb._grpc_update_agent_operation_attributes(
                data, fields=['results']))
        request = self.requests[0]
        self.assertEqual(['results'], list(request.fields))

        with mock.patch(
                'shakenfist.mariadb._direct_update_agent_operation_attributes',
                return_value=True) as direct:
            self.service.UpdateAgentOperationAttributes(request, None)
        self.assertEqual(['results'], direct.call_args.kwargs['fields'])
