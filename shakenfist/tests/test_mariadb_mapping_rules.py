# Copyright 2026 Michael Still and contributors
#
# Unit tests for the joined mapping rule accessor: the three-layer
# stack of _direct_find_mapping_rules, _grpc_find_mapping_rules and
# find_mapping_rules, and the servicer between them.
#
# Patterns mirrored:
#   - test_mariadb_namespace_keys.py (the equivalent joined accessor
#     for namespace keys, including its _MockEngine direct-path tests
#     and its public-router tests)

from unittest import mock
from uuid import UUID

import grpc

from shakenfist.daemons.database import main as database_main
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.protos import database_pb2
from shakenfist.schema.mapping_rule_attributes import MappingRuleAttributesData
from shakenfist.schema.mapping_rule_data import MappingRuleData
from shakenfist.tests import base

from sqlalchemy.exc import OperationalError


RULE_UUID_1 = UUID('ccccdddd-0000-4000-8000-000000000001')
RULE_UUID_2 = UUID('ccccdddd-0000-4000-8000-000000000002')

_SAMPLE_RULE = MappingRuleData(
    uuid=RULE_UUID_1, namespace='ci', name='ryll', version=1)

_SAMPLE_ATTRS = MappingRuleAttributesData(
    uuid=RULE_UUID_1,
    issuer='github',
    bound_claims={'repository': 'shakenfist/ryll'},
    scopes=['blob.read'],
    key_ttl=3600,
    key_name_prefix='ryll-ci')


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class _MockRow:
    """Minimal row stub with attribute access for column values."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _MockResult:
    """Minimal result stub supporting fetchall()."""

    def __init__(self, rows=None):
        self._rows = rows if rows is not None else []

    def fetchall(self):
        return self._rows


class _MockConnection:
    """Connection stub that records every execute() call."""

    def __init__(self, result=None, error=None):
        self.result = result or _MockResult()
        self.error = error
        self.executed = []

    def execute(self, stmt, *args, **kwargs):
        self.executed.append(stmt)
        if self.error:
            raise self.error
        return self.result

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _MockEngine:
    """Engine stub returning a fixed connection for connect()."""

    def __init__(self, connection):
        self._connection = connection

    def connect(self):
        return self._connection


def _row(rule_uuid=RULE_UUID_1, namespace='ci', name='ryll', version=1,
         issuer='github', bound_claims='{"repository": "shakenfist/ryll"}',
         scopes='["blob.read"]', key_ttl=3600, key_name_prefix='ryll-ci'):
    return _MockRow(
        uuid=rule_uuid, namespace=namespace, name=name, version=version,
        issuer=issuer, bound_claims=bound_claims, scopes=scopes,
        key_ttl=key_ttl, key_name_prefix=key_name_prefix)


def _operational_error():
    return OperationalError('SELECT 1', {}, Exception('gone away'))


class _FakeRpcError(grpc.RpcError):
    """Minimal grpc.RpcError stand-in, as test_mariadb_coalescing has."""


# ---------------------------------------------------------------------------
# Direct accessor (SQL pushdown of the issuer filter)
# ---------------------------------------------------------------------------

class DirectFindMappingRulesTestCase(base.ShakenFistTestCase):
    """Tests for _direct_find_mapping_rules()."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_the_policy_arrives_without_a_second_read(self, mock_get_engine):
        """One query returns the static row and the whole policy."""
        conn = _MockConnection(result=_MockResult(rows=[_row()]))
        mock_get_engine.return_value = _MockEngine(conn)

        results = mariadb._direct_find_mapping_rules('github')

        self.assertEqual(1, len(conn.executed))
        self.assertEqual(1, len(results))
        static, attrs = results[0]
        self.assertEqual('ci', static.namespace)
        self.assertEqual('ryll', static.name)
        self.assertEqual('github', attrs.issuer)
        self.assertEqual({'repository': 'shakenfist/ryll'}, attrs.bound_claims)
        self.assertEqual(['blob.read'], attrs.scopes)
        self.assertEqual(3600, attrs.key_ttl)
        self.assertEqual('ryll-ci', attrs.key_name_prefix)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_query_joins_static_to_attributes_on_uuid(self, mock_get_engine):
        """The listing is a single joined query, not an N+1 read."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_find_mapping_rules('github')

        sql = str(conn.executed[0])
        self.assertIn('JOIN mapping_rule_attributes', sql)
        self.assertIn(
            'mapping_rule_attributes.uuid = mapping_rules.uuid', sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_a_rule_without_a_policy_is_dropped_by_the_join(
            self, mock_get_engine):
        """The join is inner, so a rule with no attributes row is gone.

        A rule whose policy row is missing says nothing about which
        identities it accepts, so it must not be returned as a rule
        with an absent policy -- an inner join is how it disappears,
        and an outer one would hand the caller a rule to interpret.
        """
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_find_mapping_rules(None)

        sql = str(conn.executed[0])
        self.assertIn('FROM mapping_rules JOIN mapping_rule_attributes', sql)
        self.assertNotIn('LEFT OUTER JOIN', sql)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_the_issuer_filter_is_pushed_into_sql(self, mock_get_engine):
        """The issuer predicate is in the WHERE clause, bound to the arg."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_find_mapping_rules('github')

        sql = str(conn.executed[0])
        self.assertIn('WHERE mapping_rule_attributes.issuer = ', sql)
        params = conn.executed[0].compile().params
        self.assertIn('github', params.values())

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_another_issuers_rule_is_not_excluded_in_python(
            self, mock_get_engine):
        """Nothing re-checks the issuer after the query.

        Paired with the test above: the predicate is in the SQL, and
        this one proves there is no second filter in Python which would
        make the SQL one redundant and hide its loss.
        """
        conn = _MockConnection(result=_MockResult(rows=[
            _row(),
            _row(rule_uuid=RULE_UUID_2, name='other', issuer='keycloak'),
        ]))
        mock_get_engine.return_value = _MockEngine(conn)

        results = mariadb._direct_find_mapping_rules('github')

        self.assertEqual(
            ['github', 'keycloak'], [attrs.issuer for _, attrs in results])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_no_issuer_returns_every_rule(self, mock_get_engine):
        """issuer=None leaves the issuer predicate out entirely."""
        conn = _MockConnection(result=_MockResult(rows=[
            _row(),
            _row(rule_uuid=RULE_UUID_2, name='other', issuer='keycloak'),
        ]))
        mock_get_engine.return_value = _MockEngine(conn)

        results = mariadb._direct_find_mapping_rules(None)

        sql = str(conn.executed[0])
        self.assertNotIn('WHERE', sql)
        self.assertEqual(2, len(results))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_an_unreadable_rule_is_skipped_and_the_rest_returned(
            self, mock_get_engine):
        """One damaged row does not refuse the whole listing.

        The point reads raise for the rule the caller named. Here the
        rule is one of many, and a single unreadable row must not
        refuse every request this accessor serves. Skipping only ever
        withholds a grant.
        """
        conn = _MockConnection(result=_MockResult(rows=[
            _row(rule_uuid=RULE_UUID_2, name='damaged',
                 bound_claims='not json at all'),
            _row(),
        ]))
        mock_get_engine.return_value = _MockEngine(conn)

        results = mariadb._direct_find_mapping_rules('github')

        self.assertEqual(['ryll'], [static.name for static, _ in results])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_a_rule_with_no_claims_at_all_is_skipped(self, mock_get_engine):
        """A NULL bound_claims is damage, not a matcher set."""
        conn = _MockConnection(result=_MockResult(rows=[
            _row(bound_claims=None),
        ]))
        mock_get_engine.return_value = _MockEngine(conn)

        self.assertEqual([], mariadb._direct_find_mapping_rules('github'))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_result_returns_empty_list(self, mock_get_engine):
        """No matching rows means an empty list, not None."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        self.assertEqual([], mariadb._direct_find_mapping_rules('github'))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_a_database_failure_raises_rather_than_listing_nothing(
            self, mock_get_engine):
        """An outage must not read as "no rule accepts that provider"."""
        conn = _MockConnection(error=_operational_error())
        mock_get_engine.return_value = _MockEngine(conn)

        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb._direct_find_mapping_rules, 'github')


# ---------------------------------------------------------------------------
# The servicer
# ---------------------------------------------------------------------------

class FindMappingRulesServicerTestCase(base.ShakenFistTestCase):
    """Tests for the sf-database handler."""

    def setUp(self):
        super().setUp()
        self.service = database_main.DatabaseService.__new__(
            database_main.DatabaseService)
        self.service.monitor = mock.MagicMock()

    @mock.patch('shakenfist.mariadb._direct_find_mapping_rules',
                return_value=[])
    def test_an_unset_issuer_means_every_rule(self, mock_direct):
        """Field presence, not the empty string, is what "all" means."""
        self.service.FindMappingRules(
            database_pb2.FindMappingRulesRequest(), mock.MagicMock())

        mock_direct.assert_called_once_with(None)

    @mock.patch('shakenfist.mariadb._direct_find_mapping_rules',
                return_value=[])
    def test_the_issuer_is_passed_through(self, mock_direct):
        self.service.FindMappingRules(
            database_pb2.FindMappingRulesRequest(issuer='github'),
            mock.MagicMock())

        mock_direct.assert_called_once_with('github')
        self.service.monitor.counters[
            'find_mapping_rules'].inc.assert_called_once()

    @mock.patch('shakenfist.mariadb._direct_find_mapping_rules',
                side_effect=exceptions.DatabaseUnavailable('gone'))
    def test_a_failure_is_reported_as_internal(self, mock_direct):
        """An empty reply alone would read as a decision."""
        context = mock.MagicMock()

        reply = self.service.FindMappingRules(
            database_pb2.FindMappingRulesRequest(issuer='github'), context)

        self.assertEqual([], list(reply.rules))
        context.set_code.assert_called_once()


# ---------------------------------------------------------------------------
# The gRPC client path
# ---------------------------------------------------------------------------

class GrpcFindMappingRulesTestCase(base.ShakenFistTestCase):
    """Tests for _grpc_find_mapping_rules()."""

    def _call(self, issuer, reply):
        stub = mock.MagicMock()
        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=stub), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           return_value=reply) as mock_call:
            results = mariadb._grpc_find_mapping_rules(issuer)
        return mock_call.call_args.args[1], results

    def test_the_issuer_field_is_set_only_when_filtering(self):
        request, _ = self._call(
            'github', database_pb2.FindMappingRulesReply(rules=[]))
        self.assertTrue(request.HasField('issuer'))
        self.assertEqual('github', request.issuer)

        request, _ = self._call(
            None, database_pb2.FindMappingRulesReply(rules=[]))
        self.assertFalse(request.HasField('issuer'))

    def test_a_transport_failure_raises_database_unavailable(self):
        stub = mock.MagicMock()
        with mock.patch('shakenfist.mariadb._get_database_stub',
                        return_value=stub), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           side_effect=_FakeRpcError()):
            self.assertRaises(
                exceptions.DatabaseUnavailable,
                mariadb._grpc_find_mapping_rules, 'github')

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_the_two_paths_agree(self, mock_get_engine):
        """A rule read through the service is the rule read directly.

        The direct rows are served to the servicer, its reply is served
        to the client wrapper, and the pairs that come out are compared
        with the pairs the direct accessor produced from the same rows.
        """
        rows = [
            _row(),
            _row(rule_uuid=RULE_UUID_2, namespace='ops', name='admins',
                 bound_claims='{"groups": ["ops", "sre"]}',
                 scopes='["instance.read", "instance.write"]',
                 key_ttl=900, key_name_prefix='ops-admin'),
        ]
        conn = _MockConnection(result=_MockResult(rows=rows))
        mock_get_engine.return_value = _MockEngine(conn)
        direct = mariadb._direct_find_mapping_rules('github')

        service = database_main.DatabaseService.__new__(
            database_main.DatabaseService)
        service.monitor = mock.MagicMock()
        with mock.patch('shakenfist.mariadb._direct_find_mapping_rules',
                        return_value=direct):
            reply = service.FindMappingRules(
                database_pb2.FindMappingRulesRequest(issuer='github'),
                mock.MagicMock())

        _, through_grpc = self._call('github', reply)

        self.assertEqual(2, len(through_grpc))
        self.assertEqual(direct, through_grpc)


# ---------------------------------------------------------------------------
# The public router
# ---------------------------------------------------------------------------

class FindMappingRulesRoutingTestCase(base.ShakenFistTestCase):
    """find_mapping_rules() dispatches on the database service."""

    @mock.patch('shakenfist.mariadb._grpc_find_mapping_rules',
                return_value=[(_SAMPLE_RULE, _SAMPLE_ATTRS)])
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_find_routes_to_grpc(self, mock_use_svc, mock_grpc):
        self.assertEqual(
            [(_SAMPLE_RULE, _SAMPLE_ATTRS)],
            mariadb.find_mapping_rules('github'))
        mock_grpc.assert_called_once_with('github')

    @mock.patch('shakenfist.mariadb._direct_find_mapping_rules',
                return_value=[(_SAMPLE_RULE, _SAMPLE_ATTRS)])
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_find_routes_to_direct(self, mock_use_svc, mock_direct):
        self.assertEqual(
            [(_SAMPLE_RULE, _SAMPLE_ATTRS)],
            mariadb.find_mapping_rules('github'))
        mock_direct.assert_called_once_with('github')

    @mock.patch('shakenfist.mariadb._direct_find_mapping_rules',
                return_value=[])
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_the_issuer_filter_defaults_to_every_rule(
            self, mock_use_svc, mock_direct):
        mariadb.find_mapping_rules()
        mock_direct.assert_called_once_with(None)
