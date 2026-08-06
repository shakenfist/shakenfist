# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Live-MariaDB tests for the federation_replay primary key.

These tests run only when SF_MARIADB_TEST_DSN points at a disposable
MariaDB database, the same arrangement as the ENUM widening tests --
CI provides one via tools/ci-enum-widening-test.sh, developers can
point at a local instance.

The replay defence is a composite primary key on (token_id,
rule_uuid), inserted unconditionally so that a duplicate key error
*is* the detection. That makes the column's collation part of the
security behaviour rather than a formatting detail, and it is not
something a unit test can check: whether 'AbC' and 'abc' are the same
key is decided by MariaDB, not by SQLAlchemy. Asserting that the
Python column carries collation='utf8mb4_bin' would only prove we
wrote it down.

Under the server default (utf8mb4_general_ci, or utf8mb4_uca1400_ai_ci
on MariaDB 11.4+) comparison is case insensitive and PAD SPACE
collations ignore trailing whitespace, so an issuer minting mixed case
base64 jti values would have two distinct tokens collide and the
second, entirely legitimate, exchange would be refused as a replay.

The trailing space test is the reason these run against a server
rather than asserting the declaration. utf8mb4_bin looks like the
obvious fix and is only half of one -- it compares case sensitively
but is still PAD SPACE, so 'x' and 'x ' stay one key. That failure is
only visible from MariaDB, which is the whole argument for this file.

DESTRUCTIVE: tables in the target database are dropped during cleanup.
Never point SF_MARIADB_TEST_DSN at a real deployment.
"""

import os
import unittest
from uuid import uuid4

import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.tests import base


DSN_ENV = 'SF_MARIADB_TEST_DSN'

TEST_TABLES = [
    'federation_replay',
    'schema_versions',
]


@unittest.skipUnless(
    os.environ.get(DSN_ENV),
    f'{DSN_ENV} not set; requires a disposable MariaDB database')
class FederationReplayKeyLiveTestCase(base.ShakenFistTestCase):
    """Prove the replay key compares tokens as bytes."""

    def setUp(self):
        super().setUp()
        self.engine = sa.create_engine(os.environ[DSN_ENV])
        self.addCleanup(self._drop_tables)
        self.addCleanup(self.engine.dispose)

        mariadb._ensure_schema_versions_table(self.engine)
        mariadb._ensure_federation_replay_schema(self.engine)
        self.table = mariadb._get_federation_replay_table()
        self.rule_uuid = uuid4()

    def _drop_tables(self):
        with self.engine.connect() as conn:
            for table in TEST_TABLES:
                conn.execute(sa.text(f'DROP TABLE IF EXISTS {table}'))
            conn.commit()

    def _claim(self, token_id):
        """Insert one (token, rule) pair, returning whether it was new."""
        with self.engine.connect() as conn:
            try:
                conn.execute(self.table.insert().values(
                    token_id=token_id, rule_uuid=self.rule_uuid,
                    expires_at=1.0))
                conn.commit()
                return True
            except sa.exc.IntegrityError:
                conn.rollback()
                return False

    def test_the_column_is_declared_binary(self):
        with self.engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    'SELECT COLLATION_NAME FROM information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA = DATABASE() '
                    "AND TABLE_NAME = 'federation_replay' "
                    "AND COLUMN_NAME = 'token_id'")).first()
        self.assertIsNotNone(row)
        self.assertEqual('utf8mb4_nopad_bin', row[0])

    def test_jtis_differing_only_in_case_are_distinct_tokens(self):
        # The behaviour the collation exists for. Under a case
        # insensitive collation the second insert raises IntegrityError
        # and the exchange refuses a token nobody has used.
        self.assertTrue(self._claim('AbCdEf0123'))
        self.assertTrue(self._claim('abcdef0123'))

    def test_a_trailing_space_is_not_the_same_token(self):
        # PAD SPACE collations compare 'x' and 'x ' as equal.
        self.assertTrue(self._claim('token-with-pad'))
        self.assertTrue(self._claim('token-with-pad '))

    def test_the_same_token_twice_is_still_a_replay(self):
        # The control. Without this, a column so permissive that
        # nothing ever collides would pass both tests above, and the
        # replay defence would be gone entirely.
        self.assertTrue(self._claim('exactly-the-same'))
        self.assertFalse(self._claim('exactly-the-same'))

    def test_one_token_may_still_reach_two_rules(self):
        # Refusal is per (token, rule): exchanging one identity against
        # two rules to reach two namespaces is a legitimate pattern.
        self.assertTrue(self._claim('shared-token'))
        self.rule_uuid = uuid4()
        self.assertTrue(self._claim('shared-token'))
