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


EDGE = {'op_type': 'agentoperation',
        'op_uuid': 'e3e1f4ba-6d1a-4e39-9f1a-1c6e5a7b0d21',
        'settle_seconds': 30.0}


def _static(deadline, progress_timeout, depends_on=None, runs_after=None):
    return AgentOperationData(
        uuid=AOP_UUID,
        namespace='banana',
        instance_uuid=INSTANCE_UUID,
        commands=[{'command': 'execute'}],
        depends_on=depends_on or [],
        runs_after=runs_after or [],
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
                          expiry_reason=None, dependency_failure=None):
        data = AgentOperationAttributesData(
            uuid=AOP_UUID,
            results={'0': {'status': 0}},
            last_progress=last_progress,
            attempts=attempts,
            expiry_reason=expiry_reason,
            dependency_failure=dependency_failure)
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

    def test_edge_lists_survive(self):
        # The edges cross as JSON strings rather than with field presence,
        # because an operation which names nothing has an empty list and
        # '[]' says so unambiguously -- there is no second state for
        # presence to distinguish.
        data = _static(None, None, depends_on=[EDGE], runs_after=[EDGE])
        proto = self.service._agentop_to_proto(data)
        out = self.service._agentop_from_proto(proto)
        self.assertEqual([EDGE], out.depends_on)
        self.assertEqual([EDGE], out.runs_after)

    def test_no_edges_crosses_as_an_empty_list(self):
        data = _static(None, None)
        proto = self.service._agentop_to_proto(data)
        self.assertEqual('[]', proto.depends_on_json)
        self.assertEqual('[]', proto.runs_after_json)
        out = self.service._agentop_from_proto(proto)
        self.assertEqual([], out.depends_on)
        self.assertEqual([], out.runs_after)

    def test_the_edge_lists_are_not_transposed(self):
        # The two fields are adjacent, same-typed and set by adjacent
        # lines, which is the shape a copy-paste swaps. A round trip of
        # two equal lists could not tell.
        data = _static(None, None, depends_on=[EDGE])
        out = self.service._agentop_from_proto(
            self.service._agentop_to_proto(data))
        self.assertEqual([EDGE], out.depends_on)
        self.assertEqual([], out.runs_after)

    def test_attributes_dependency_failure_none_stays_none(self):
        # Absent means SQL NULL: no dependency failure reached this
        # operation. That is a different fact from a cascade which
        # recorded nothing, so the two must not collapse.
        proto, out = self._round_trip_attrs(None, 0)
        self.assertFalse(proto.HasField('dependency_failure_json'))
        self.assertIsNone(out.dependency_failure)

    def test_attributes_empty_dependency_failure_is_not_none(self):
        proto, out = self._round_trip_attrs(
            None, 0, dependency_failure={})
        self.assertTrue(proto.HasField('dependency_failure_json'))
        self.assertEqual({}, out.dependency_failure)

    def test_attributes_dependency_failure_survives(self):
        failure = {'op_type': 'agentoperation', 'op_uuid': str(AOP_UUID),
                   'state': 'error'}
        _, out = self._round_trip_attrs(
            None, 0, dependency_failure=failure)
        self.assertEqual(failure, out.dependency_failure)


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
        # An unset proto3 string is the empty string, not '[]', so both
        # readers test the string before decoding it. Without that guard
        # json.loads('') raises and an old peer's request fails outright
        # rather than arriving with no edges.
        self.assertEqual('', proto.depends_on_json)
        self.assertEqual([], out.depends_on)
        self.assertEqual([], out.runs_after)

    def test_attributes_message_without_new_fields(self):
        proto = database_pb2.AgentOperationAttributesProto(
            uuid=str(AOP_UUID),
            results_json='{}')
        out = self.service._agentop_attrs_from_proto(proto)
        self.assertIsNone(out.last_progress)
        self.assertEqual(0, out.attempts)
        self.assertIsNone(out.expiry_reason)
        self.assertIsNone(out.dependency_failure)


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

    def test_create_request_carries_the_edge_lists(self):
        self.assertTrue(mariadb._grpc_create_agent_operation(
            _static(None, None, depends_on=[EDGE])))
        data = self.requests[0].data
        self.assertEqual('[]', data.runs_after_json)
        out = self.service._agentop_from_proto(data)
        self.assertEqual([EDGE], out.depends_on)
        self.assertEqual([], out.runs_after)

    def test_get_reply_reads_an_empty_edge_string_as_no_edges(self):
        # An older sf-database answers with the field unset, which on the
        # wire is the empty string rather than '[]'. The client reader has
        # its own guard for that, separate from the servicer's, and this
        # is the one which would raise out of json.loads without it.
        reply_data = self.service._agentop_to_proto(_static(None, None))
        reply_data.ClearField('depends_on_json')
        reply_data.ClearField('runs_after_json')
        self.reply = mock.MagicMock(found=True, data=reply_data)
        out = mariadb._grpc_get_agent_operation(AOP_UUID)
        self.assertEqual([], out.depends_on)
        self.assertEqual([], out.runs_after)

    def test_attributes_update_request_carries_dependency_failure(self):
        # The servicer rebuilds the model from the message and then writes
        # the columns the mask names, so a masked update which did not
        # populate the field would write NULL over whatever was there.
        failure = {'op_type': 'agentoperation', 'op_uuid': str(AOP_UUID),
                   'state': 'error'}
        data = AgentOperationAttributesData(
            uuid=AOP_UUID, results={}, attempts=0,
            dependency_failure=failure)
        self.assertTrue(mariadb._grpc_update_agent_operation_attributes(
            data, fields=['dependency_failure']))
        request = self.requests[0]
        self.assertEqual(['dependency_failure'], list(request.fields))
        self.assertTrue(request.data.HasField('dependency_failure_json'))
        self.assertEqual(
            failure,
            self.service._agentop_attrs_from_proto(
                request.data).dependency_failure)

    def test_attributes_update_request_leaves_no_failure_unset(self):
        data = AgentOperationAttributesData(
            uuid=AOP_UUID, results={}, attempts=0)
        self.assertTrue(mariadb._grpc_update_agent_operation_attributes(
            data, fields=['attempts']))
        self.assertFalse(
            self.requests[0].data.HasField('dependency_failure_json'))

    def test_update_mask_survives_the_round_trip_to_the_direct_layer(self):
        # The direct path's mask is covered by the live suite and by
        # test_mariadb_instance_attributes.py, but nothing asserted the
        # mask actually crossed the wire. Without that, a client which
        # dropped `fields` would write every column -- the
        # cross-attribute lost update the field masks exist to
        # prevent -- on the path every daemon except sf-database uses.
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
