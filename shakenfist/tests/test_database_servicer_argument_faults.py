# Copyright 2026 Michael Still and contributors

"""A servicer arm which reports an argument fault must catch only that fault.

``GetNamespaceClaims`` and ``ReleaseNamespaceClaimHolds`` each answer
one specific argument mistake with ``INVALID_ARGUMENT``: a
coverage_state a claim listing cannot filter on, and a release which
named no holds and did not ask for all of them. Both arms were written
as ``except ValueError``, which is every ValueError the handler's whole
body can raise -- a stored row which will not fit its wire message, a
parse inside the event path, anything a later change to the body adds.
Those are this tier's faults, and reporting one as the caller's
argument error sends the caller away to fix a request which was
correct, with the one status code that says no retry will help
(issue 5074).

Each validator now raises its own ValueError subclass and each arm
catches that type, so an unrelated ValueError falls through to the
handler's generic arm, where it is recorded on the node which can act
on it and reported as a fault of this tier. These tests pin both
directions: the named fault still gets the argument status, and a
ValueError from anywhere else in the same body does not.

``RecordRelationship`` is the third handler the issue names. It has no
argument arm today, because it reports every failure in band through
its generic arm -- so its tests pin the types the two faults reach the
handler as, which is what an arm added later has to catch on.
"""

from unittest import mock

import grpc

from shakenfist.daemons.database import main as daemons_database_main
from shakenfist import exceptions
from shakenfist.protos import database_pb2
from shakenfist.schema.object_reference import RELATIONSHIP_VALUE_MAX_LENGTH
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.tests import base


def _claim_row(uuid='claim-uuid'):
    """One stored claim row, in the dict form the direct reader returns."""
    return {
        'uuid': uuid,
        'namespace': 'ns',
        'limit_cpus': 4,
        'limit_memory_mb': 1024,
        'limit_disk_gb': 20,
        'used_cpus': 0,
        'used_memory_mb': 0,
        'used_disk_gb': 0,
        'peak_used_cpus': 0,
        'peak_used_memory_mb': 0,
        'peak_used_disk_gb': 0,
        'state': 'active',
        'expires_at': 0.0,
        'updated_at': 0.0
    }


def _hold_row(uuid='hold-uuid'):
    """One stored hold row, in the dict form the direct reader returns."""
    return {
        'uuid': uuid,
        'claim_uuid': 'claim-uuid',
        'namespace': 'ns',
        'cpus': 1,
        'memory_mb': 512,
        'disk_gb': 10,
        'node_uuid': 'node',
        'instance_uuid': None,
        'created_at': 0.0,
        'updated_at': 0.0
    }


class GetNamespaceClaimsArgumentFaultTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())
        self.context = mock.MagicMock()

    def test_bad_coverage_state_is_an_argument_fault(self):
        # Not mocked: the real validator runs, ahead of any table or
        # engine access, which is what makes this the caller's mistake.
        request = database_pb2.GetNamespaceClaimsRequest(
            namespace='ns', coverage_state='retired')

        reply = self.servicer.GetNamespaceClaims(request, self.context)

        self.context.set_code.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT)
        self.assertIn('coverage_state', self.context.set_details.call_args[0][0])
        self.assertEqual([], list(reply.claims))

    @mock.patch('shakenfist.mariadb._direct_get_namespace_claims',
                side_effect=ValueError('something else went wrong'))
    def test_unrelated_value_error_is_this_tiers_fault(self, mock_read):
        request = database_pb2.GetNamespaceClaimsRequest(namespace='ns')

        reply = self.servicer.GetNamespaceClaims(request, self.context)

        self.context.set_code.assert_called_once_with(
            grpc.StatusCode.INTERNAL)
        self.assertEqual([], list(reply.claims))

    @mock.patch('shakenfist.mariadb._direct_get_namespace_claims')
    def test_an_unreadable_row_is_this_tiers_fault(self, mock_read):
        # The issue's own example: _claim_message() raises ValueError
        # for a stored row which will not fit its wire message. The
        # request was valid, so INVALID_ARGUMENT would be a lie and the
        # caller cannot act on it.
        mock_read.return_value = [_claim_row()]
        request = database_pb2.GetNamespaceClaimsRequest(namespace='ns')

        with mock.patch.object(daemons_database_main.DatabaseService,
                               '_claim_message',
                               side_effect=ValueError('row will not fit')):
            reply = self.servicer.GetNamespaceClaims(request, self.context)

        self.context.set_code.assert_called_once_with(
            grpc.StatusCode.INTERNAL)
        self.assertEqual([], list(reply.claims))

    @mock.patch('shakenfist.mariadb._direct_get_namespace_claims')
    def test_a_readable_row_is_not_an_error(self, mock_read):
        # The negative control. Without it the tests above pass just as
        # well against a handler which fails every listing.
        mock_read.return_value = [_claim_row()]
        request = database_pb2.GetNamespaceClaimsRequest(namespace='ns')

        reply = self.servicer.GetNamespaceClaims(request, self.context)

        self.context.set_code.assert_not_called()
        self.assertEqual(['claim-uuid'], [c.uuid for c in reply.claims])


class ReleaseNamespaceClaimHoldsArgumentFaultTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())
        self.context = mock.MagicMock()

    def test_naming_no_holds_is_an_argument_fault(self):
        # Again unmocked: the guard is the first statement of the direct
        # release, so nothing has been released when it fires.
        request = database_pb2.ReleaseNamespaceClaimHoldsRequest(
            claim_uuid='claim-uuid')

        reply = self.servicer.ReleaseNamespaceClaimHolds(
            request, self.context)

        self.context.set_code.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT)
        self.assertFalse(reply.success)

    @mock.patch('shakenfist.mariadb._direct_release_claim_holds',
                side_effect=ValueError('something else went wrong'))
    def test_unrelated_value_error_is_this_tiers_fault(self, mock_release):
        request = database_pb2.ReleaseNamespaceClaimHoldsRequest(
            claim_uuid='claim-uuid', all_holds=True)

        reply = self.servicer.ReleaseNamespaceClaimHolds(
            request, self.context)

        # The generic arm records the exception and refuses, but never
        # tells the caller its request was malformed.
        self.assertNotIn(
            mock.call(grpc.StatusCode.INVALID_ARGUMENT),
            self.context.set_code.call_args_list)
        self.assertFalse(reply.success)

    @mock.patch('shakenfist.mariadb._direct_release_claim_holds')
    def test_an_unreadable_hold_is_this_tiers_fault(self, mock_release):
        mock_release.return_value = {
            'success': True, 'error': '', 'released': [_hold_row()],
            'clamped': False, 'clamps': []}
        request = database_pb2.ReleaseNamespaceClaimHoldsRequest(
            claim_uuid='claim-uuid', all_holds=True)

        with mock.patch.object(daemons_database_main.DatabaseService,
                               '_hold_message',
                               side_effect=ValueError('row will not fit')):
            reply = self.servicer.ReleaseNamespaceClaimHolds(
                request, self.context)

        self.assertNotIn(
            mock.call(grpc.StatusCode.INVALID_ARGUMENT),
            self.context.set_code.call_args_list)
        self.assertFalse(reply.success)

    @mock.patch('shakenfist.mariadb._direct_release_claim_holds')
    def test_a_readable_release_is_not_an_error(self, mock_release):
        # The negative control for this handler.
        mock_release.return_value = {
            'success': True, 'error': '', 'released': [_hold_row()],
            'clamped': False, 'clamps': []}
        request = database_pb2.ReleaseNamespaceClaimHoldsRequest(
            claim_uuid='claim-uuid', all_holds=True)

        reply = self.servicer.ReleaseNamespaceClaimHolds(
            request, self.context)

        self.context.set_code.assert_not_called()
        self.assertTrue(reply.success)
        self.assertEqual(['hold-uuid'], [h.uuid for h in reply.released])


class RecordRelationshipArgumentFaultTestCase(base.ShakenFistTestCase):
    """The third handler, whose argument fault has its own type as well.

    This handler has no argument arm today -- every failure is reported
    in band through its generic arm -- so these tests are the floor for
    one added later. What they establish is that the two faults reach
    the handler as different types: the refusal of an over-long
    relationship_value is a ``RelationshipValueTooLong``, and a
    ValueError out of the event path in the same try body is not.
    """

    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())
        self.context = mock.MagicMock()

    def _request(self, relationship_value='0'):
        return database_pb2.RecordRelationshipRequest(
            source_type=ObjectType.INSTANCE.proto_id,
            source_uuid='instance-uuid',
            relationship=RelationshipType.DISK.proto_id,
            relationship_value=relationship_value,
            target_type=ObjectType.BLOB.proto_id,
            target_uuid='blob-uuid')

    @mock.patch('shakenfist.util.exceptions.ignore_exception')
    def test_an_over_long_value_arrives_as_its_own_type(self, mock_ignore):
        # Unmocked below the handler: the real validator is the first
        # statement of the direct writer, ahead of any engine access.
        request = self._request(
            relationship_value='a' * (RELATIONSHIP_VALUE_MAX_LENGTH + 1))

        reply = self.servicer.RecordRelationship(request, self.context)

        self.assertFalse(reply.success)
        raised = mock_ignore.call_args[0][1]
        self.assertIsInstance(raised, exceptions.RelationshipValueTooLong)

    @mock.patch('shakenfist.util.exceptions.ignore_exception')
    @mock.patch('shakenfist.eventlog.add_event_multi',
                side_effect=ValueError('something else went wrong'))
    @mock.patch('shakenfist.mariadb._direct_record_relationship',
                return_value=True)
    def test_an_event_path_value_error_is_not_an_argument_fault(
            self, mock_record, mock_event, mock_ignore):
        # The event path is inside the same try body, and nothing it
        # raises is the caller's argument mistake, so an arm written for
        # the argument mistake must not claim it.
        reply = self.servicer.RecordRelationship(self._request(),
                                                 self.context)

        self.assertFalse(reply.success)
        raised = mock_ignore.call_args[0][1]
        self.assertIsInstance(raised, ValueError)
        self.assertNotIsInstance(raised, exceptions.DatabaseArgumentError)

    @mock.patch('shakenfist.eventlog.add_event_multi')
    @mock.patch('shakenfist.mariadb._direct_record_relationship',
                return_value=True)
    def test_a_recorded_relationship_is_not_an_error(
            self, mock_record, mock_event):
        # The negative control for this handler.
        reply = self.servicer.RecordRelationship(self._request(),
                                                 self.context)

        self.assertTrue(reply.success)
        self.assertEqual('', reply.error)
