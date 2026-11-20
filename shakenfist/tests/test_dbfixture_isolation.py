# Copyright 2026 Michael Still and contributors

"""MariaDBTableFixture gives each test a private SQLAlchemy MetaData.

mariadb.py caches each table in a module global and defines it in a
module-level MetaData, so a test which reaches a table getter defines
that table process-wide. The fixture exists to undo that, and restoring
the caches is not enough on its own: a table definition cannot be
removed from a MetaData, so a getter which ran against the *outer*
MetaData leaves the table defined there while the restore puts the
cache back to None. The next register_all_tables() in that worker then
dies with "Table 'x' is already defined for this MetaData instance",
in whichever unrelated module happens to run next.

That is why the reset is in setUp() rather than in build_engine(): a
test which drives a failure path with a mocked engine never builds one,
and it reaches the getters just the same. It reached a pull request
that way (issue #4965), as an order-dependent failure three modules
later.
"""

from shakenfist import mariadb
from shakenfist.tests import base
from shakenfist.tests import dbfixture


class MetadataIsolationTestCase(dbfixture.MariaDBTableFixture,
                                base.ShakenFistTestCase):
    def test_a_fixture_test_starts_with_a_private_metadata(self):
        # Whatever ran before in this worker, the metadata and every
        # table cache start empty, so a getter reached here cannot
        # define a table anybody else will see.
        self.assertIsNone(mariadb._metadata)
        self.assertIsNone(mariadb._blob_attributes_table)

    def test_a_getter_without_an_engine_is_still_isolated(self):
        # The case the restore cannot clean up after: no engine is
        # built, and the getter still has to land in a private
        # MetaData.
        table = mariadb._get_blob_attributes_table()

        self.assertIsNotNone(mariadb._metadata)
        self.assertIn('blob_attributes', mariadb._metadata.tables)
        self.assertIs(table.metadata, mariadb._metadata)


class MetadataIsNotLeakedTestCase(base.ShakenFistTestCase):
    """The other side of the contract, from outside the fixture.

    This pollutes the module-level MetaData the way an ordinary test
    does -- by reaching a table getter with no fixture at all -- and
    then runs a fixture test which reaches a different getter. It is
    self contained rather than order dependent, because the failure it
    guards against only appears when something ran first.

    It takes the whole snapshot rather than the three globals it looks
    at, because it calls register_all_tables(), which populates every
    cache. Restoring only some of them is how this test first became
    the polluter it exists to catch: the caches it left behind pointed
    into a MetaData the restore had already replaced, so a later walk
    of the schema in another module came back empty.
    """

    def setUp(self):
        super().setUp()
        saved = dbfixture.snapshot_table_state()
        self.addCleanup(dbfixture.restore_table_state, saved)
        dbfixture.reset_table_state(saved)

    def test_a_fixture_test_leaves_the_outer_metadata_consistent(self):
        # An ordinary test, with a mocked engine and no fixture, defines
        # its table in the module MetaData. This is what
        # test_mariadb_artifacts does.
        mariadb._get_artifact_attributes_table()
        outer = mariadb._metadata
        self.assertIsNotNone(outer)
        self.assertNotIn('blob_attributes', outer.tables)

        case = MetadataIsolationTestCase(
            'test_a_getter_without_an_engine_is_still_isolated')
        result = case.run()
        self.assertTrue(result.wasSuccessful(),
                        result.errors + result.failures)

        # The fixture test reached the blob_attributes getter. Had that
        # landed in the outer MetaData, restoring the cache could not
        # undo it, and the next register_all_tables() in this worker
        # would raise "Table 'blob_attributes' is already defined".
        self.assertIs(outer, mariadb._metadata)
        self.assertNotIn('blob_attributes', mariadb._metadata.tables)
        self.assertIsNone(mariadb._blob_attributes_table)

    def test_a_walk_of_the_schema_still_sees_every_table(self):
        # The symptom from the other direction, and the one which bit:
        # every cache has to point into the MetaData a walk reads, or
        # the walk silently returns a subset. _native_enum_columns() is
        # the walk which noticed.
        case = MetadataIsolationTestCase(
            'test_a_getter_without_an_engine_is_still_isolated')
        self.assertTrue(case.run().wasSuccessful())

        found = {table for table, _column in mariadb._native_enum_columns()}
        self.assertIn('object_states', found)
