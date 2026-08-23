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

from uuid import UUID

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

    def _round_trip_attrs(self, last_progress, attempts):
        data = AgentOperationAttributesData(
            uuid=AOP_UUID,
            results={'0': {'status': 0}},
            last_progress=last_progress,
            attempts=attempts)
        proto = self.service._agentop_attrs_to_proto(data)
        return proto, self.service._agentop_attrs_from_proto(proto)

    def test_attributes_none_stays_none(self):
        proto, out = self._round_trip_attrs(None, 0)
        self.assertFalse(proto.HasField('last_progress'))
        self.assertIsNone(out.last_progress)
        self.assertEqual(0, out.attempts)
        self.assertEqual({'0': {'status': 0}}, out.results)

    def test_attributes_zero_progress_is_not_none(self):
        proto, out = self._round_trip_attrs(0.0, 3)
        self.assertTrue(proto.HasField('last_progress'))
        self.assertEqual(0.0, out.last_progress)
        self.assertEqual(3, out.attempts)

    def test_attributes_values_survive(self):
        _, out = self._round_trip_attrs(1787427490.5, 2)
        self.assertEqual(1787427490.5, out.last_progress)
        self.assertEqual(2, out.attempts)


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
