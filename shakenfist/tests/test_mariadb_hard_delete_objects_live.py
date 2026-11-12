# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""The batch hard delete against a real MariaDB.

Every case is in shakenfist/tests/hard_delete_cases.py, which this module
runs against MariaDB and
``test_mariadb_hard_delete_objects_engine`` runs against sqlite. This
module runs only when SF_MARIADB_TEST_DSN points at a disposable MariaDB
database (CI provides one via tools/ci-enum-widening-test.sh, which runs
every test_mariadb_*_live module behind one MariaDB install; developers
can point at a local instance), which in CI means the merge queue. What
it adds over the sqlite backend is the dialect: the real column types,
the real migrations which build them, and MariaDB's own transaction and
locking behaviour.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment. The
test_mariadb_*_live modules share one database, so they must be run
serially -- ``stestr run --serial``, as CI does.
"""

import os
import unittest
from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.tests import base
from shakenfist.tests import hard_delete_cases


DSN_ENV = 'SF_MARIADB_TEST_DSN'

# The tables the batch deletes from, created in the order ensure_schema()
# creates them.
ENSURES = [
    mariadb._ensure_object_states_schema,
    mariadb._ensure_ipam_reservations_schema,
    mariadb._ensure_uploads_schema,
    mariadb._ensure_dnsmasq_schema,
    mariadb._ensure_blobs_schema,
    mariadb._ensure_object_references_schema,
    mariadb._ensure_blob_hashes_schema,
    mariadb._ensure_blob_transfers_schema,
    mariadb._ensure_blob_attributes_schema,
    # The namespace key migration reads namespace attributes.
    mariadb._ensure_namespaces_schema,
    mariadb._ensure_namespace_attributes_schema,
    mariadb._ensure_namespace_key_attributes_schema,
    mariadb._ensure_namespace_keys_schema,
    mariadb._ensure_trusted_issuer_attributes_schema,
    mariadb._ensure_trusted_issuers_schema,
    mariadb._ensure_mapping_rule_attributes_schema,
    mariadb._ensure_mapping_rules_schema,
    mariadb._ensure_artifacts_schema,
    mariadb._ensure_artifact_attributes_schema,
    mariadb._ensure_artifact_indexes_schema,
    mariadb._ensure_network_interfaces_schema,
    mariadb._ensure_network_interface_attributes_schema,
    mariadb._ensure_networks_schema,
    mariadb._ensure_network_attributes_schema,
    mariadb._ensure_ipams_schema,
    mariadb._ensure_agent_operations_schema,
    mariadb._ensure_agent_operation_attributes_schema,
    mariadb._ensure_object_metadata_schema,
    mariadb._ensure_cluster_operation_targets_schema,
    mariadb._ensure_cluster_operations_schema,
    mariadb._ensure_cluster_operation_errors_schema,
    mariadb._ensure_event_objects_schema,
]

TEST_TABLES = [
    'object_states', 'ipam_reservations', 'uploads', 'dnsmasq', 'blobs',
    'object_references', 'blob_hashes', 'blob_transfers', 'blob_attributes',
    'namespaces', 'namespace_attributes', 'namespace_key_attributes',
    'namespace_keys', 'trusted_issuer_attributes',
    'trusted_issuers', 'mapping_rule_attributes', 'mapping_rules', 'artifacts',
    'artifact_attributes', 'artifact_indexes', 'network_interfaces',
    'network_interface_attributes', 'networks', 'network_attributes', 'ipams',
    'agent_operations', 'agent_operation_attributes', 'object_metadata',
    'cluster_operation_targets', 'cluster_operations',
    'cluster_operation_errors', 'event_objects', 'schema_versions',
]


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class HardDeleteObjectsLiveTestCase(hard_delete_cases.HardDeleteObjectsCases,
                                    base.ShakenFistTestCase):
    """Run every hard delete case against a real MariaDB."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self._drop_tables)
        self._drop_tables()

        mariadb._ensure_schema_versions_table(self.engine)
        for ensure in ENSURES:
            ensure(self.engine)

        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()
