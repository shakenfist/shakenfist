# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""The batch hard delete against an in-memory engine, on every pull request.

Every case is in shakenfist/tests/hard_delete_cases.py, which this module
runs against sqlite and ``test_mariadb_hard_delete_objects_live`` runs
against a real MariaDB. The tables are built from mariadb.py's own
``sa.Table`` definitions and the statements under test are the ones which
ship, so a builder keyed on the wrong column, or a type whose delete set
is missing a table, fails here -- which is the point: before #4880 the
only tests which executed the batch's SQL needed a MariaDB and so ran
nowhere but the merge queue, after each change to the batch had been
reviewed and approved.

What this backend cannot show is anything dialect-specific: sqlite is not
MariaDB, it does not run the migrations which build the production
columns, and its transaction and locking behaviour is its own. The live
module is still the authority on all of that, and still gates a merge.
"""

from unittest import mock

from shakenfist import mariadb
from shakenfist.tests import base
from shakenfist.tests import dbfixture
from shakenfist.tests import hard_delete_cases


# Every table a case puts a row in, derived from the row descriptions
# rather than listed again.
TABLE_GETTERS = sorted(
    {get_table for get_table, _ in
     list(hard_delete_cases.ROWS.values())
     + list(hard_delete_cases.BASE_ROWS.values())},
    key=lambda getter: getter.__name__)


class HardDeleteObjectsEngineTestCase(dbfixture.MariaDBTableFixture,
                                      hard_delete_cases.HardDeleteObjectsCases,
                                      base.ShakenFistTestCase):
    """Run every hard delete case against an in-memory sqlite engine."""

    def setUp(self):
        super().setUp()
        # cluster_operation_targets.sequence_number is a BIGINT
        # AUTO_INCREMENT key which production INSERTs leave out, so sqlite
        # has to assign it the way MariaDB does.
        self.engine = self.build_engine(TABLE_GETTERS,
                                        autoincrement_bigint_keys=True)
        self.addCleanup(self.engine.dispose)

        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

        # The fixture's config is a MagicMock, whose every attribute is
        # truthy, and a truthy MARIADB_GATEWAY_HOSTS sends the public
        # functions to the gRPC tier. The direct path is the one with
        # statements in it, and the one the live backend takes.
        mariadb.config.MARIADB_GATEWAY_HOSTS = None
