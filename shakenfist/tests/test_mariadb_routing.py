# Copyright 2019 Michael Still and contributors
#
# Tests for mariadb._use_database_service(), which decides whether a
# MariaDB access is routed through the sf-database gRPC tier or made
# directly against MariaDB.

from unittest import mock

from shakenfist import mariadb
from shakenfist.tests import base
from shakenfist.util import caller_identity


class TestUseDatabaseService(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        # The caller identity is a process global set once at daemon
        # startup, so restore whatever this process had after each test.
        original = caller_identity.get_caller_daemon()
        self.addCleanup(caller_identity.set_caller_identity, original)

    def _config(self, mariadb_host='', gateway_hosts=None):
        return mock.patch.multiple(
            'shakenfist.mariadb.config',
            MARIADB_HOST=mariadb_host,
            MARIADB_GATEWAY_HOSTS=(
                gateway_hosts if gateway_hosts is not None else []))

    def test_no_gateway_hosts_uses_direct(self):
        # Nothing to route to, so whatever direct access exists is used.
        caller_identity.set_caller_identity('api')
        with self._config(mariadb_host='10.0.0.1'):
            self.assertFalse(mariadb._use_database_service())

    def test_no_gateway_hosts_and_no_direct_uses_direct(self):
        caller_identity.set_caller_identity('api')
        with self._config():
            self.assertFalse(mariadb._use_database_service())

    def test_gateway_hosts_without_direct_uses_tier(self):
        caller_identity.set_caller_identity('api')
        with self._config(gateway_hosts=['10.0.0.1']):
            self.assertTrue(mariadb._use_database_service())

    def test_database_daemon_uses_direct(self):
        # sf-database must not route through itself.
        caller_identity.set_caller_identity('database')
        with self._config(mariadb_host='10.0.0.1',
                          gateway_hosts=['10.0.0.1']):
            self.assertFalse(mariadb._use_database_service())

    def test_ctl_uses_direct(self):
        # sf-ctl runs ensure-mariadb-schema and initialise-node before
        # sf-database has started, so it cannot depend on the tier.
        caller_identity.set_caller_identity('ctl')
        with self._config(mariadb_host='10.0.0.1',
                          gateway_hosts=['10.0.0.1']):
            self.assertFalse(mariadb._use_database_service())

    def test_other_daemons_on_a_database_node_use_the_tier(self):
        # /etc/sf/config is the shared EnvironmentFile for every daemon on
        # a node, so on a database-tier node MARIADB_HOST is visible to all
        # of them. Only sf-database and sf-ctl may act on it; anything else
        # going direct silently bypasses the tier, which is both contrary
        # to the documented architecture and invisible to sf-database's
        # per-caller request metrics.
        for daemon in ['api', 'cleaner', 'cluster', 'net', 'queues',
                       'resources', 'sidechannel', 'transfers', 'unknown']:
            caller_identity.set_caller_identity(daemon)
            with self._config(mariadb_host='10.0.0.1',
                              gateway_hosts=['10.0.0.1']):
                self.assertTrue(
                    mariadb._use_database_service(),
                    'daemon %s should route through the database tier'
                    % daemon)

    def test_instance_attribute_reads_route_through_the_tier(self):
        # The end to end consequence of the above: an API worker on a
        # database node must issue GetInstanceAttributes over gRPC, which
        # is what makes its load visible in database_requests_total.
        caller_identity.set_caller_identity('api')
        with self._config(mariadb_host='10.0.0.1',
                          gateway_hosts=['10.0.0.1']):
            with mock.patch(
                    'shakenfist.mariadb._grpc_get_instance_attributes',
                    return_value=None) as mock_grpc:
                with mock.patch(
                        'shakenfist.mariadb._direct_get_instance_attributes',
                        return_value=None) as mock_direct:
                    mariadb.get_instance_attributes(
                        mock.sentinel.instance_uuid)

        mock_grpc.assert_called_once_with(mock.sentinel.instance_uuid)
        mock_direct.assert_not_called()
