# Copyright 2026 Michael Still and contributors
#
# Direct SQL tests for the cluster operation coalescing primitives
# (issue #3878).
#
# These execute the queries against a real database rather than a mocked
# engine. That distinction is the whole point of the module: the existing
# coalescing tests mock either ``mariadb.claim_coalescible_siblings`` (in
# shakenfist/tests/operations/test_baseoperation.py) or ``_get_engine``
# (in shakenfist/tests/test_mariadb_work_queue.py), so between them they
# assert that the dispatcher decides to call the primitive and that the
# statement has the right shape -- but never that the statement matches a
# row. Coalescing was consequently dead for three months while the suite
# stayed green.
#
# The specific trap is the one described in test_mariadb_orphans.py:
# object_states.object_uuid stores the dashed 36 character form, while
# cluster_operations.uuid is a sa.Uuid column storing undashed CHAR(32)
# on both sqlite and MariaDB. A join between them has to transform one
# side or it silently never matches.

import json
import uuid
from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.tests import base


def _register_mariadb_json_shims(engine):
    """Teach sqlite the two JSON functions the coalescing queries use.

    The queries are written for MariaDB and call JSON_LENGTH and
    JSON_UNQUOTE, neither of which sqlite has -- its JSON1 extension
    spells the first ``json_array_length`` and has no need of the
    second. Registering shims lets the *production statement* run here
    unmodified, which is the only way a test can prove the join matches.
    Rewriting the query for sqlite would prove nothing about the query
    that actually ships.
    """
    @sa.event.listens_for(engine, 'connect')
    def _on_connect(dbapi_connection, _record):
        def json_length(doc, path=None):
            if doc is None:
                return None
            value = json.loads(doc)
            if path is not None:
                # Only the '$.key' form is used by these queries.
                value = value.get(path[2:]) if isinstance(value, dict) else None
            if value is None:
                return None
            return len(value)

        def json_unquote(value):
            # MariaDB's JSON_EXTRACT yields a quoted JSON scalar which
            # JSON_UNQUOTE strips; sqlite's already yields the bare
            # value, so tolerate both.
            if value is None or not isinstance(value, str):
                return value
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                return json.loads(value)
            return value

        def unix_timestamp(value=None):
            # The fold stamps update_time with MariaDB's
            # UNIX_TIMESTAMP(NOW(6)); sqlite has neither function. The
            # value is not what these tests assert on, only that the
            # UPDATE runs, so a fixed stamp is enough.
            return 1_750_000_000.0

        dbapi_connection.create_function('json_length', -1, json_length)
        dbapi_connection.create_function('json_unquote', 1, json_unquote)
        dbapi_connection.create_function('unix_timestamp', -1, unix_timestamp)
        dbapi_connection.create_function('now', -1, lambda *_: None)


NETWORK_UUID = '11111111-1111-4111-8111-111111111111'
SURVIVOR_UUID = '99999999-9999-4999-8999-999999999999'
SIBLING_UUID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
TASK = 'network_apply_update_dnsmasq'


class CoalescingSQLTestCase(base.ShakenFistTestCase):
    """The coalescing queries, run against a real database."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', mock.MagicMock())
        self.config.start()
        self.addCleanup(self.config.stop)

        # Building a private engine means clearing mariadb's cached table
        # objects and its MetaData, which are module globals. Left
        # cleared, the module would afterwards hold a MetaData describing
        # only the two tables built here, and any later test in the same
        # worker which walks the full schema sees a truncated one. Snapshot
        # everything table-shaped and put it back.
        self._saved = {
            name: getattr(mariadb, name)
            for name in dir(mariadb)
            if name.endswith('_table') and name.startswith('_')}
        self._saved['_metadata'] = mariadb._metadata
        self.addCleanup(self._restore_module_state)

    def _restore_module_state(self):
        for name, value in self._saved.items():
            setattr(mariadb, name, value)

    def _build_engine(self):
        for attr in ('_object_states_table', '_cluster_operations_table'):
            setattr(mariadb, attr, None)
        mariadb._metadata = None

        engine = sa.create_engine('sqlite:///:memory:')
        _register_mariadb_json_shims(engine)
        built = [mariadb._get_object_states_table(),
                 mariadb._get_cluster_operations_table()]
        built[0].metadata.create_all(engine, tables=built)
        return engine

    def _insert_op(self, conn, op_uuid, tasks=(TASK,), state='queued',
                   created_at=100.0):
        """Insert one cluster operation and its state row.

        The two rows deliberately go in the way production writes them:
        the static row through the sa.Uuid bind processor, which stores
        the undashed form, and the state row as the dashed string every
        caller hands to set_state().
        """
        ops = mariadb._get_cluster_operations_table()
        states = mariadb._get_object_states_table()
        conn.execute(sa.insert(ops).values(
            uuid=uuid.UUID(op_uuid),
            operation_type='net_op',
            created_at=created_at,
            network_uuid=uuid.UUID(NETWORK_UUID),
            priority='user_facing',
            metadata_json={'tasks': list(tasks)}))
        conn.execute(sa.insert(states).values(
            object_uuid=op_uuid,
            object_type='net_op',
            state_value=state,
            update_time=created_at,
            message=None))

    def test_the_two_tables_really_do_store_uuids_differently(self):
        # The premise every other test here rests on. If this ever stops
        # being true the join transformation becomes unnecessary rather
        # than wrong, and the rest of this module would start passing for
        # the wrong reason.
        engine = self._build_engine()
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID)
            conn.commit()

            stored_op = conn.execute(
                sa.text('select uuid from cluster_operations')).scalar()
            stored_state = conn.execute(
                sa.text('select object_uuid from object_states')).scalar()

        self.assertEqual(SIBLING_UUID.replace('-', ''), stored_op)
        self.assertEqual(SIBLING_UUID, stored_state)
        self.assertNotEqual(stored_op, stored_state)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_matches_a_queued_sibling(self, mock_get_engine):
        # Issue #3878: this returned None for three months because the
        # join compared an undashed uuid to a dashed one.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID)
            conn.commit()

        self.assertEqual(
            SIBLING_UUID,
            mariadb._direct_find_existing_coalescible_op(
                'net_op', 'network_uuid', NETWORK_UUID, TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_returns_the_oldest_of_several(
            self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID, created_at=50.0)
            self._insert_op(conn, SIBLING_UUID, created_at=150.0)
            conn.commit()

        self.assertEqual(
            SURVIVOR_UUID,
            mariadb._direct_find_existing_coalescible_op(
                'net_op', 'network_uuid', NETWORK_UUID, TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_ignores_a_non_queued_op(self, mock_get_engine):
        # An op already executing must not be reused: its work may have
        # started before the new caller's change was written.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SIBLING_UUID, state='executing')
            conn.commit()

        self.assertIsNone(
            mariadb._direct_find_existing_coalescible_op(
                'net_op', 'network_uuid', NETWORK_UUID, TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_find_existing_ignores_a_multi_task_op(self, mock_get_engine):
        # A sibling carrying extra work must not be folded away, or the
        # extra work is silently dropped.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(
                conn, SIBLING_UUID, tasks=(TASK, 'network_apply_ensure_mesh'))
            conn.commit()

        self.assertIsNone(
            mariadb._direct_find_existing_coalescible_op(
                'net_op', 'network_uuid', NETWORK_UUID, TASK))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claim_siblings_folds_and_excludes_the_survivor(
            self, mock_get_engine):
        # The other half of #3878, and the one with teeth: this marks
        # other workers' operations complete.
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID)
            self._insert_op(conn, SIBLING_UUID)
            conn.commit()

        folded = mariadb._direct_claim_coalescible_siblings(
            'net_op', 'network_uuid', NETWORK_UUID, [TASK], SURVIVOR_UUID)
        self.assertEqual([SIBLING_UUID], folded)

        states = mariadb._get_object_states_table()
        with engine.connect() as conn:
            rows = dict(conn.execute(sa.select(
                states.c.object_uuid, states.c.state_value)).fetchall())
        self.assertEqual('complete', rows[SIBLING_UUID])
        self.assertEqual('queued', rows[SURVIVOR_UUID])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claim_siblings_folds_nothing_when_alone(self, mock_get_engine):
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID)
            conn.commit()

        self.assertEqual(
            [], mariadb._direct_claim_coalescible_siblings(
                'net_op', 'network_uuid', NETWORK_UUID, [TASK],
                SURVIVOR_UUID))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_claim_siblings_leaves_another_network_alone(
            self, mock_get_engine):
        # The target filter is what stops a fold reaching across
        # networks; with the join broken this was untested in practice.
        other_network = '22222222-2222-4222-8222-222222222222'
        engine = self._build_engine()
        mock_get_engine.return_value = engine
        ops = mariadb._get_cluster_operations_table()
        with engine.connect() as conn:
            self._insert_op(conn, SURVIVOR_UUID)
            self._insert_op(conn, SIBLING_UUID)
            conn.execute(sa.update(ops)
                         .where(ops.c.uuid == uuid.UUID(SIBLING_UUID))
                         .values(network_uuid=uuid.UUID(other_network)))
            conn.commit()

        self.assertEqual(
            [], mariadb._direct_claim_coalescible_siblings(
                'net_op', 'network_uuid', NETWORK_UUID, [TASK],
                SURVIVOR_UUID))
