# Copyright 2019 Michael Still and contributors
#
# Issue 4863: a network in error or delete_wait was counted as already
# gone by the namespace delete handler, because it asked
# Network.is_dead(). Neither state is in FINAL_OBJECT_STATES, so the
# deleted object sweep never reaps either, which left a network with no
# namespace, no owner and nothing which would ever remove it -- along
# with whatever it holds on the network node. The REST delete handler now
# refuses a namespace which still holds one (see
# shakenfist/tests/external_api/test_auth.py), and
# Namespace.hard_delete() is the backstop for whatever arrives anyway.
from unittest import mock

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.namespace import Namespace
from shakenfist.network.network import Network
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class HardDeleteSweepsNetworksTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        for name in ['system', 'doomed', 'bystander']:
            self.mock_mariadb.create_namespace(name, 'key1', 'secret')

    def _hard_delete_doomed(self):
        with mock.patch.object(Network, 'delete_on_network_node',
                               autospec=True) as dn:
            Namespace.from_db('doomed').hard_delete()
        return [str(c.args[0].uuid) for c in dn.call_args_list]

    def test_errored_network_is_deleted(self):
        # Hard deleting the row here would leave the vxlan, the netns,
        # the dnsmasq and the NAT rules on the network node with nothing
        # left to explain them, so the delete the network never got is
        # enqueued instead.
        net = self.mock_mariadb.create_network(
            name='orphan', namespace='doomed', set_state=dbo.STATE_ERROR)

        self.assertEqual([str(net.uuid)], self._hard_delete_doomed())

    def test_delete_wait_network_is_deleted(self):
        # A delete which was still in flight when the namespace went.
        net = self.mock_mariadb.create_network(
            name='draining', namespace='doomed',
            set_state=dbo.STATE_DELETE_WAIT)

        self.assertEqual([str(net.uuid)], self._hard_delete_doomed())

    def test_live_network_is_deleted(self):
        # A create which raced the namespace delete.
        net = self.mock_mariadb.create_network(
            name='racer', namespace='doomed', set_state=dbo.STATE_CREATED)

        self.assertEqual([str(net.uuid)], self._hard_delete_doomed())

    def test_deleted_network_is_left_alone(self):
        # It has released everything it held, and the deleted object
        # sweep hard deletes its row by state rather than by namespace.
        # Unlike the instance iterator, the network one yields deleted
        # networks, so this is the test which proves the skip exists.
        self.mock_mariadb.create_network(
            name='gone', namespace='doomed', set_state=dbo.STATE_DELETED)

        self.assertEqual([], self._hard_delete_doomed())

    def test_another_namespaces_network_is_left_alone(self):
        self.mock_mariadb.create_network(
            name='theirs', namespace='bystander', set_state=dbo.STATE_ERROR)

        self.assertEqual([], self._hard_delete_doomed())

    def test_an_empty_namespace_is_not_an_error(self):
        self.assertEqual([], self._hard_delete_doomed())

    def test_the_network_records_why_it_was_deleted(self):
        self.mock_mariadb.create_network(
            name='orphan', namespace='doomed', set_state=dbo.STATE_ERROR)

        with mock.patch.object(Network, 'delete_on_network_node',
                               autospec=True):
            with mock.patch.object(Network, 'add_event') as ae:
                Namespace.from_db('doomed').hard_delete()

        messages = [c.args[1] for c in ae.call_args_list]
        self.assertIn(
            'deletion request from namespace hard delete, network '
            'outlived its namespace', messages)

    def test_system_does_not_sweep_the_cluster(self):
        # The object iterator reads a namespace of 'system' as "every
        # namespace", so without the guard this would enqueue a delete
        # for both of these.
        self.mock_mariadb.create_network(
            name='theirs', namespace='bystander', set_state=dbo.STATE_ERROR)
        self.mock_mariadb.create_network(
            name='ours', namespace='system', set_state=dbo.STATE_CREATED)

        with mock.patch.object(Network, 'delete_on_network_node',
                               autospec=True) as dn:
            Namespace.from_db('system').hard_delete()
        self.assertEqual([], dn.call_args_list)

    def test_a_failed_enqueue_does_not_defer_trust_revocation(self):
        # Enqueueing the delete raises when the operation cannot be
        # written. That aborts the pass, which is fine -- the namespace
        # rows survive for the sweep's next one -- but the trust
        # revocation must already have happened.
        Namespace.from_db('bystander').add_trust('doomed')
        self.mock_mariadb.create_network(
            name='orphan', namespace='doomed', set_state=dbo.STATE_ERROR)

        with mock.patch.object(
                Network, 'delete_on_network_node', autospec=True,
                side_effect=exceptions.DatabaseUnavailable('no')) as dn:
            self.assertRaises(exceptions.DatabaseUnavailable,
                              Namespace.from_db('doomed').hard_delete)
        dn.assert_called_once()
        self.assertNotIn('doomed', Namespace.from_db('bystander').trust)
