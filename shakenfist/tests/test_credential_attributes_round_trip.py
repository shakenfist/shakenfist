# Copyright 2026 Michael Still and contributors

"""A failed credential attributes read, servicer to client and back.

Both halves of this are covered on their own: the servicer answers
INTERNAL rather than an empty reply, and the client's wrapper turns an
RpcError into DatabaseUnavailable rather than None. Nothing drove one
through the other, so nothing held them to the same contract at the
protocol boundary -- and that boundary is the whole of issue 3836. A
key, issuer or rule attributes read which answers "no such row" for a
database failure tells the orphan reconciler that a live credential is
garbage, and the reconciler then marks it deleted.

The server is a real in-process gRPC server, the same approach as
test_database_health_integration.py, because a mocked stub cannot show
that a status code set on one side is the exception raised on the
other.
"""

import concurrent.futures
from unittest import mock
from uuid import UUID

import grpc

from shakenfist.daemons.database import main as daemons_database_main
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.protos import database_pb2_grpc
from shakenfist.tests import base


KEY_UUID = UUID('11111111-1111-4111-8111-111111111111')


class CredentialAttributesRoundTripTestCase(base.ShakenFistTestCase):
    # (what fails on the server, what the client calls, the SQL-ish
    # fragment the server's exception message carries).
    READERS = [
        ('_direct_get_namespace_key_attributes',
         '_grpc_get_namespace_key_attributes',
         'namespace key attributes'),
        ('_direct_get_trusted_issuer_attributes',
         '_grpc_get_trusted_issuer_attributes',
         'trusted issuer attributes'),
        ('_direct_get_mapping_rule_attributes',
         '_grpc_get_mapping_rule_attributes',
         'mapping rule attributes'),
    ]

    def setUp(self):
        super().setUp()
        self.server = grpc.server(
            concurrent.futures.ThreadPoolExecutor(max_workers=1))
        database_pb2_grpc.add_DatabaseServiceServicer_to_server(
            daemons_database_main.DatabaseService(mock.MagicMock()),
            self.server)
        port = self.server.add_insecure_port('127.0.0.1:0')
        self.server.start()
        self.addCleanup(lambda: self.server.stop(grace=0).wait())

        # http_proxy is disabled so a loopback connection is not routed
        # through an ambient proxy, which CI runners do set.
        self.channel = grpc.insecure_channel(
            f'127.0.0.1:{port}',
            options=[('grpc.enable_http_proxy', 0)])
        self.addCleanup(self.channel.close)
        self.stub = database_pb2_grpc.DatabaseServiceStub(self.channel)

    # What these readers raise when MariaDB goes away: the SQLAlchemy
    # error in the message, statement text and bound uuid included.
    BOOM = exceptions.DatabaseUnavailable(
        'attributes could not be read: (pymysql.err.OperationalError) '
        'server has gone away [SQL: SELECT namespace_key_attributes.uuid '
        "...] [parameters: {'uuid_1': '" + str(KEY_UUID) + "'}]")

    def _client_reads(self, wrapper, direct, **direct_behaviour):
        """Call a client wrapper against the real servicer.

        The server's direct reader is patched with direct_behaviour,
        and the client's stub is pointed at the in-process server.
        """
        with mock.patch(f'shakenfist.mariadb.{direct}', **direct_behaviour), \
                mock.patch('shakenfist.mariadb._get_database_stub',
                           return_value=self.stub):
            return getattr(mariadb, wrapper)(KEY_UUID)

    def test_a_failed_read_arrives_as_database_unavailable(self):
        # Not as None, which every one of these callers reads as "no
        # such row" (issue 3836).
        for direct, wrapper, _ in self.READERS:
            with self.subTest(reader=direct):
                with self.assertRaises(exceptions.DatabaseUnavailable):
                    self._client_reads(wrapper, direct, side_effect=self.BOOM)

    def test_the_failure_does_not_carry_the_statement_back(self):
        # The server's own message names the statement and the bound
        # uuid. That is for the server's log, not for a status detail
        # which becomes the client's exception message and from there
        # its logs and events.
        for direct, wrapper, expected in self.READERS:
            with self.subTest(reader=direct):
                with self.assertRaises(exceptions.DatabaseUnavailable) as cm:
                    self._client_reads(wrapper, direct, side_effect=self.BOOM)

                raised = str(cm.exception)
                self.assertIn(f'{expected} could not be read', raised)
                self.assertNotIn('SQL:', raised)
                self.assertNotIn('parameters:', raised)

    def test_a_damaged_rule_arrives_as_a_damaged_rule(self):
        # The one arm which answers found=True with a sentinel field
        # rather than a status code, so the one a proto or handler
        # change is most likely to break. Read as an outage, the API
        # could no longer mark the rule unusable; read as absent, the
        # reconciler would mark a live rule deleted.
        direct, wrapper, _ = self.READERS[2]
        with self.assertRaises(exceptions.CorruptMappingRule):
            self._client_reads(
                wrapper, direct,
                side_effect=exceptions.CorruptMappingRule(
                    f'mapping rule {KEY_UUID} has undecodable scopes'))

    def test_a_missing_row_is_still_an_answer(self):
        # The negative control. Without it the tests above pass just as
        # well against a client which raises for every reply.
        for direct, wrapper, _ in self.READERS:
            with self.subTest(reader=direct):
                self.assertIsNone(
                    self._client_reads(wrapper, direct, return_value=None))
