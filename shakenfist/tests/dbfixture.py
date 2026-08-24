# Copyright 2026 Michael Still and contributors
#
# Shared fixture for tests which run mariadb.py's real statements against
# a real (sqlite) database rather than a mocked engine.

import json
from unittest import mock

import sqlalchemy as sa

from shakenfist import mariadb


class MariaDBTableFixture:
    """Give a test its own sqlite engine built from mariadb's own metadata.

    Tests which want to prove a statement *matches a row* -- rather than
    that it has the right shape -- have to build the tables from
    mariadb.py's own ``sa.Table`` definitions, so the column types under
    test are the ones that ship. Doing that means clearing mariadb's
    cached table objects and its ``MetaData``, all of which are module
    globals, and the clearing is what makes this a fixture rather than a
    helper function: left cleared, the module afterwards holds a
    ``MetaData`` describing only the handful of tables one test built, and
    any later test in the same worker which walks the full schema sees a
    truncated one. That is a real failure this suite has had, and it only
    shows up when the tests run together, never when one is run alone.

    Mix this in ahead of the base test case and call ``build_engine()``.
    """

    def setUp(self):
        super().setUp()

        self._saved_mariadb_state = {
            name: getattr(mariadb, name)
            for name in dir(mariadb)
            # The table *caches*, not the _get_*_table functions which
            # populate them -- hence the callable() test.
            if (name.startswith('_') and name.endswith('_table')
                and not callable(getattr(mariadb, name)))}
        self._saved_mariadb_state['_metadata'] = mariadb._metadata
        self.addCleanup(self._restore_mariadb_state)

        self.mariadb_config = mock.patch(
            'shakenfist.mariadb.config', mock.MagicMock())
        self.mariadb_config.start()
        self.addCleanup(self.mariadb_config.stop)

    def _restore_mariadb_state(self):
        for name, value in self._saved_mariadb_state.items():
            setattr(mariadb, name, value)

    def build_engine(self, table_getters, json_shims=False):
        """Build an in-memory sqlite engine holding the named tables.

        ``table_getters`` is a list of mariadb's ``_get_*_table``
        functions. Their caches are cleared first so the tables are
        rebuilt against a fresh ``MetaData``.
        """
        for name in self._saved_mariadb_state:
            if name != '_metadata':
                setattr(mariadb, name, None)
        mariadb._metadata = None

        engine = sa.create_engine('sqlite:///:memory:')
        if json_shims:
            register_mariadb_json_shims(engine)
        built = [getter() for getter in table_getters]
        built[0].metadata.create_all(engine, tables=built)
        return engine


def register_mariadb_json_shims(engine):
    """Teach sqlite the MariaDB functions the coalescing queries use.

    The queries are written for MariaDB and call JSON_LENGTH,
    JSON_UNQUOTE and UNIX_TIMESTAMP, none of which sqlite has -- its
    JSON1 extension spells the first ``json_array_length`` and has no
    need of the second. Registering shims lets the *production
    statement* run here unmodified, which is the only way a test can
    prove the join matches. Rewriting the query for sqlite would prove
    nothing about the query that actually ships.
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
