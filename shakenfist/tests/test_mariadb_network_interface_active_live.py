# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB tests for the network_interfaces.active backfill.

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh,
which runs every test_mariadb_*_live module behind one MariaDB install;
developers can point at a local instance), which in CI means the merge
queue.

The v2 to v3 migration's backfill is an ``UPDATE ... LEFT JOIN`` whose
join strips the dashes out of an object key, which is MariaDB syntax
sqlite does not have, so a unit test can only assert its shape against
a mocked connection. What it cannot show is what issue 4988 is about:
that a row the backfill did not reach is held by no UNIQUE constraint
at all, because ``active IS NULL`` collides with nothing. So these
tests build the v2 shape, upgrade it, and look at the rows and the
constraints afterwards -- including after a backfill which failed.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment. The
test_mariadb_*_live modules share one database, so they must be run
serially -- ``stestr run --serial``, as CI does.
"""

import os
import unittest
from unittest import mock
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

TEST_TABLES = [
    'network_interfaces',
    'object_states',
    'schema_versions',
]

MACADDR_CONSTRAINT = 'uq_network_interfaces_macaddr'
COMPOSITE_CONSTRAINT = 'uq_network_interfaces_macaddr_active_network'


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class NetworkInterfaceActiveBackfillLiveTestCase(base.ShakenFistTestCase):
    """Upgrade a version 2 network_interfaces table and look at it."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        mariadb._ensure_schema_versions_table(self.engine)
        mariadb._ensure_object_states_schema(self.engine)
        mariadb._ensure_network_interfaces_schema(self.engine)

        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.interfaces = mariadb._get_network_interfaces_table()
        self.states = mariadb._get_object_states_table()
        self.networks = {}
        self._regress_to_version_2()

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _regress_to_version_2(self):
        """Turn the table the ensure built back into its v2 shape.

        A version 2 table has no ``active`` column and a global UNIQUE
        on macaddr, which is what a cluster upgrading onto the composite
        constraint actually has.
        """
        with self.engine.connect() as conn:
            conn.execute(sa.text(
                f'ALTER TABLE network_interfaces '
                f'DROP INDEX {COMPOSITE_CONSTRAINT}'))
            conn.execute(sa.text(
                'ALTER TABLE network_interfaces DROP COLUMN active'))
            conn.execute(sa.text(
                f'ALTER TABLE network_interfaces '
                f'ADD CONSTRAINT {MACADDR_CONSTRAINT} UNIQUE (macaddr)'))
            conn.commit()
        mariadb._set_table_version(self.engine, 'network_interfaces', 2)

    def _write(self, macaddr, state=None, network_uuid=None):
        """One v2-shaped interface row, optionally with a state row."""
        interface_uuid = uuid4()
        self.networks[interface_uuid] = network_uuid or uuid4()
        with self.engine.connect() as conn:
            conn.execute(sa.insert(self.interfaces).values(
                uuid=interface_uuid,
                network_uuid=self.networks[interface_uuid],
                instance_uuid=uuid4(),
                macaddr=macaddr,
                ipv4='10.0.0.5',
                order=0,
                model='virtio',
                version=5))
            if state is not None:
                conn.execute(sa.insert(self.states).values(
                    object_type=ObjectType.INTERFACE,
                    object_uuid=str(interface_uuid),
                    state_value=state,
                    update_time=0.0))
            conn.commit()
        return interface_uuid

    def _claim_mac_of(self, interface_uuid):
        """Insert a live interface reusing another row's MAC and network.

        Returns True if the database accepted it, which is the question
        a UNIQUE constraint over ``active`` exists to answer. An inert
        constraint answers yes to every caller.
        """
        with self.engine.connect() as conn:
            existing = conn.execute(
                sa.select(self.interfaces.c.macaddr).where(
                    self.interfaces.c.uuid == interface_uuid)).first()[0]
            try:
                conn.execute(sa.insert(self.interfaces).values(
                    uuid=uuid4(),
                    network_uuid=self.networks[interface_uuid],
                    instance_uuid=uuid4(),
                    macaddr=existing,
                    ipv4='10.0.0.6',
                    order=1,
                    model='virtio',
                    version=5,
                    active=True))
                conn.commit()
                return True
            except IntegrityError:
                conn.rollback()
                return False

    def _active(self, interface_uuid):
        with self.engine.connect() as conn:
            return conn.execute(
                sa.select(self.interfaces.c.active).where(
                    self.interfaces.c.uuid == interface_uuid)).first()[0]

    def _constraints(self):
        with self.engine.connect() as conn:
            return {row[0] for row in conn.execute(sa.text(
                'SELECT CONSTRAINT_NAME FROM '
                'information_schema.TABLE_CONSTRAINTS '
                'WHERE TABLE_SCHEMA = DATABASE() '
                "AND TABLE_NAME = 'network_interfaces'")).fetchall()}

    def test_the_upgrade_fills_active_and_swaps_the_constraint(self):
        live = self._write('1a:91:64:d2:15:01', state='created')
        deleted = self._write('1a:91:64:d2:15:02', state='deleted')
        stateless = self._write('1a:91:64:d2:15:03')

        result = mariadb._ensure_network_interfaces_schema(self.engine)

        self.assertEqual(3, result['end_version'])
        self.assertEqual(
            3, mariadb._get_table_version(self.engine, 'network_interfaces'))
        self.assertTrue(self._active(live))
        self.assertIsNone(self._active(deleted))
        # No state row reads as "not deleted", which is the safe way
        # round: the row participates in the constraint.
        self.assertTrue(self._active(stateless))

        constraints = self._constraints()
        self.assertIn(COMPOSITE_CONSTRAINT, constraints)
        self.assertNotIn(MACADDR_CONSTRAINT, constraints)

        # What the constraint is for, both ways round: a row the
        # backfill marked live holds its MAC, and one it marked deleted
        # does not.
        self.assertFalse(self._claim_mac_of(live))
        self.assertTrue(self._claim_mac_of(deleted))

    def test_a_failed_backfill_leaves_the_old_constraint_in_place(self):
        """The whole of issue 4988, seen from the database.

        Stamping version 3 here would leave a table whose MAC
        constraint holds none of its rows -- every one of them has
        ``active IS NULL``, and NULLs do not collide -- so the
        migration aborts instead and the cluster keeps the constraint
        it already had.
        """
        live = self._write('1a:91:64:d2:15:04', state='created')

        with mock.patch('shakenfist.mariadb._migration_backfill_batched',
                        side_effect=OperationalError(
                            'UPDATE', {}, Exception('lock wait timeout'))):
            self.assertRaises(
                OperationalError,
                mariadb._ensure_network_interfaces_schema, self.engine)

        self.assertEqual(
            2, mariadb._get_table_version(self.engine, 'network_interfaces'))
        self.assertIsNone(self._active(live))
        constraints = self._constraints()
        self.assertIn(MACADDR_CONSTRAINT, constraints)
        self.assertNotIn(COMPOSITE_CONSTRAINT, constraints)

        # The point of all of it: the live interface's MAC is still
        # spoken for. Landing the composite constraint over an unfilled
        # ``active`` would have let this insert through.
        self.assertFalse(self._claim_mac_of(live))

    def test_the_retry_after_a_failed_backfill_finishes_the_job(self):
        """A table left at version 2 is still upgradable.

        The ``active IS NULL`` predicate is what makes this true of the
        rows as well as of the version: the retry picks up exactly the
        rows the aborted run did not reach.
        """
        live = self._write('1a:91:64:d2:15:05', state='created')
        deleted = self._write('1a:91:64:d2:15:06', state='deleted')

        with mock.patch('shakenfist.mariadb._migration_backfill_batched',
                        side_effect=OperationalError(
                            'UPDATE', {}, Exception('lock wait timeout'))):
            self.assertRaises(
                OperationalError,
                mariadb._ensure_network_interfaces_schema, self.engine)

        result = mariadb._ensure_network_interfaces_schema(self.engine)

        self.assertEqual(2, result['start_version'])
        self.assertEqual(3, result['end_version'])
        self.assertTrue(self._active(live))
        self.assertIsNone(self._active(deleted))
        self.assertIn(COMPOSITE_CONSTRAINT, self._constraints())
