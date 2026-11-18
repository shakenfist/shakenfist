# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB test for the network_interfaces v3 to v4 step (issue 4946).

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database (CI provides one via tools/ci-enum-widening-test.sh;
developers can point at a local instance).

The unit tests for this step run on sqlite or against a recorded list of
statements, and sqlite only happens to share MariaDB's NULL-distinct
UNIQUE semantics. The parts of the fix which only a real server can
prove are here: the ALTER with its backtick-quoted reserved word, the
information_schema probe which decides whether version 4 is stamped,
the repair's ``active IS TRUE`` reads, and the constraint refusing a
second live claim on one index while letting deleted rows share one.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment.
"""

import os
import unittest
import uuid
from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.network.interface import NetworkInterface
from shakenfist.schema.network_interface_data import NetworkInterfaceData
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'
CONSTRAINT = 'uq_network_interfaces_instance_order_active'
TEST_TABLES = ['network_interfaces', 'schema_versions']


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class NetworkInterfaceOrderLiveTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self._drop_tables)
        self.addCleanup(self.engine.dispose)

        self._drop_tables()
        mariadb._ensure_schema_versions_table(self.engine)
        mariadb._ensure_network_interfaces_schema(self.engine)

        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.table = mariadb._get_network_interfaces_table()
        self.instance = uuid.uuid4()
        self.network = uuid.uuid4()

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _has_constraint(self):
        with self.engine.connect() as conn:
            return mariadb._table_has_constraint(
                conn, 'network_interfaces', CONSTRAINT)

    def _downgrade_to_version_3(self):
        """What an upgrading cluster hands the step: no index constraint."""
        with self.engine.connect() as conn:
            conn.execute(sa.text(
                f'ALTER TABLE network_interfaces DROP INDEX {CONSTRAINT}'))
            conn.commit()
        mariadb._set_table_version(self.engine, 'network_interfaces', 3)
        self.assertFalse(self._has_constraint())

    def _insert(self, order, macaddr, active=True, instance=None):
        ni_uuid = uuid.uuid4()
        with self.engine.connect() as conn:
            conn.execute(sa.insert(self.table).values(
                uuid=ni_uuid, network_uuid=self.network,
                instance_uuid=instance or self.instance, macaddr=macaddr,
                ipv4=None, order=order, model='virtio',
                version=NetworkInterface.current_version, active=active))
            conn.commit()
        return ni_uuid

    def _orders(self):
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.select(self.table.c.uuid, self.table.c.order)
                .where(self.table.c.instance_uuid == self.instance)
                .where(self.table.c.active.is_(True))).fetchall()
        return {row.uuid: row.order for row in rows}

    def _ni(self, order, macaddr):
        return NetworkInterfaceData(
            uuid=uuid.uuid4(), network_uuid=self.network,
            instance_uuid=self.instance, macaddr=macaddr, ipv4=None,
            order=order, model='virtio',
            version=NetworkInterface.current_version)

    def test_a_fresh_table_is_built_with_the_constraint(self):
        # The probe is what tells "already here" from "could not be
        # added", so it must find the constraint the CREATE TABLE made.
        self.assertTrue(self._has_constraint())
        self.assertEqual(
            mariadb.NETWORK_INTERFACES_VERSION,
            mariadb._get_table_version(self.engine, 'network_interfaces'))

    def test_the_upgrade_repairs_collisions_and_lands_the_constraint(self):
        self._downgrade_to_version_3()
        self._insert(0, '02:00:00:00:00:01')
        self._insert(0, '02:00:00:00:00:02')
        self._insert(1, '02:00:00:00:00:03')
        # Deleted rows share an index freely and are not repaired.
        self._insert(0, '02:00:00:00:00:04', active=None)
        self._insert(0, '02:00:00:00:00:05', active=None)

        result = mariadb._ensure_network_interfaces_schema(self.engine)

        self.assertEqual(3, result['start_version'])
        self.assertEqual(4, result['end_version'])
        self.assertTrue(self._has_constraint())
        self.assertEqual(
            4, mariadb._get_table_version(self.engine, 'network_interfaces'))

        # One claimant of index 0 keeps it, and the other is moved above
        # everything the instance uses.
        self.assertEqual([0, 1, 2], sorted(self._orders().values()))

    def test_the_constraint_refuses_a_second_live_claim(self):
        self._downgrade_to_version_3()
        mariadb._ensure_network_interfaces_schema(self.engine)

        self.assertTrue(mariadb._direct_create_network_interface(
            self._ni(2, '02:00:00:00:00:01')))
        self.assertFalse(mariadb._direct_create_network_interface(
            self._ni(2, '02:00:00:00:00:02')))

    def test_a_deleted_interface_releases_its_index(self):
        first = self._ni(2, '02:00:00:00:00:01')
        self.assertTrue(mariadb._direct_create_network_interface(first))
        with self.engine.connect() as conn:
            conn.execute(sa.update(self.table)
                         .where(self.table.c.uuid == first.uuid)
                         .values(active=None))
            conn.commit()

        self.assertTrue(mariadb._direct_create_network_interface(
            self._ni(2, '02:00:00:00:00:02')))

    def test_a_rerun_at_version_4_changes_nothing(self):
        result = mariadb._ensure_network_interfaces_schema(self.engine)
        self.assertFalse(result['migrated'])
        self.assertTrue(self._has_constraint())
