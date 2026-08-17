# Copyright 2026 Michael Still and contributors

"""The database servicer must not answer a failed read with an empty list.

Both of these RPCs return a bare `repeated string`, so the reply has no
room to say "the read failed" -- an empty list is the only thing the
client can see, and it reads as an authoritative "there are none".

That is load bearing rather than tidy. `get_active_blob_uuids()` hands
its result to the cleaner as a *complement* set: every blob file on disk
whose uuid is absent from the list is unlinked. Answering a failed read
with [] is therefore an instruction to delete the node's entire blob
store (#3638).

The client-side half of that fix (raising DatabaseUnavailable rather
than flattening None) cannot help here, because this path never produces
a None for it to see: `_direct_get_objects_by_state()` returns None on
OperationalError -- MariaDB down, connection dropped, lock wait timeout,
deadlock -- and the servicer used to turn that into an OK reply with
zero uuids. That is the failure an operator is most likely to hit, since
it happens while sf-database itself is healthy and answering.
"""

from unittest import mock

import grpc

from shakenfist.daemons.database import main as daemons_database_main
from shakenfist.protos import database_pb2
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


class GetObjectsByStateFailedReadTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())
        self.request = database_pb2.GetObjectsByStateRequest(
            object_type=ObjectType.BLOB.proto_id,
            state_values=['initial', 'created'])

    @mock.patch('shakenfist.mariadb.get_objects_by_state', return_value=None)
    def test_failed_read_is_an_error_status_not_an_empty_reply(self, mock_get):
        context = mock.MagicMock()

        reply = self.servicer.GetObjectsByState(self.request, context)

        # UNAVAILABLE specifically: _grpc_call retries it and raises
        # DatabaseUnavailable once the budget is spent, which is the
        # shape get_active_blob_uuids() and the cluster sweeps handle.
        context.set_code.assert_called_once_with(
            grpc.StatusCode.UNAVAILABLE)
        self.assertEqual([], list(reply.object_uuids))

    @mock.patch('shakenfist.mariadb.get_objects_by_state', return_value=[])
    def test_genuinely_empty_is_not_an_error(self, mock_get):
        # The negative control. Without this the test above passes just
        # as well against a servicer that fails every read.
        context = mock.MagicMock()

        reply = self.servicer.GetObjectsByState(self.request, context)

        context.set_code.assert_not_called()
        self.assertEqual([], list(reply.object_uuids))

    @mock.patch('shakenfist.mariadb.get_objects_by_state',
                return_value=['uuid-1', 'uuid-2'])
    def test_results_are_returned_unchanged(self, mock_get):
        context = mock.MagicMock()

        reply = self.servicer.GetObjectsByState(self.request, context)

        context.set_code.assert_not_called()
        self.assertEqual(['uuid-1', 'uuid-2'], list(reply.object_uuids))

    @mock.patch('shakenfist.mariadb.get_objects_by_state',
                side_effect=ValueError('boom'))
    def test_unexpected_exception_is_internal_not_empty(self, mock_get):
        # INTERNAL rather than UNAVAILABLE: a bug in the handler is not a
        # transient outage, so there is nothing to gain from retrying it.
        # It is non-retryable, so the client wrapper maps it to None --
        # still a failed read, which is the point.
        context = mock.MagicMock()

        reply = self.servicer.GetObjectsByState(self.request, context)

        context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
        self.assertEqual([], list(reply.object_uuids))

    def test_unknown_object_type_is_a_bad_request(self):
        # Proto id 0 is UNSPECIFIED. "No objects of a type I do not
        # recognise" is the same silent empty answer by another route.
        context = mock.MagicMock()

        reply = self.servicer.GetObjectsByState(
            database_pb2.GetObjectsByStateRequest(
                object_type=0, state_values=['created']),
            context)

        context.set_code.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT)
        self.assertEqual([], list(reply.object_uuids))


class GetStatelessObjectUuidsFailedReadTestCase(base.ShakenFistTestCase):
    """Same contract, milder consequence.

    A failed read here makes orphan reconciliation silently repair
    nothing rather than delete anything, but the caller still cannot
    tell it apart from "this type has no zombies", and a reconcile sweep
    that quietly stops running is how orphans stay invisible to every
    state-driven iterator.
    """

    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())
        self.request = database_pb2.GetStatelessObjectUuidsRequest(
            object_type=ObjectType.NETWORK.proto_id)

    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids',
                return_value=None)
    def test_failed_read_is_an_error_status_not_an_empty_reply(self, mock_get):
        context = mock.MagicMock()

        reply = self.servicer.GetStatelessObjectUuids(self.request, context)

        context.set_code.assert_called_once_with(
            grpc.StatusCode.UNAVAILABLE)
        self.assertEqual([], list(reply.object_uuids))

    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids',
                return_value=[])
    def test_genuinely_empty_is_not_an_error(self, mock_get):
        context = mock.MagicMock()

        reply = self.servicer.GetStatelessObjectUuids(self.request, context)

        context.set_code.assert_not_called()
        self.assertEqual([], list(reply.object_uuids))

    @mock.patch('shakenfist.mariadb.get_stateless_object_uuids',
                side_effect=ValueError('boom'))
    def test_unexpected_exception_is_internal_not_empty(self, mock_get):
        context = mock.MagicMock()

        reply = self.servicer.GetStatelessObjectUuids(self.request, context)

        context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
        self.assertEqual([], list(reply.object_uuids))
