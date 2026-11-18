# Copyright 2026 Michael Still and contributors
"""Tests for ``Instance.interfaces`` as the only way to find an
instance's interfaces.

``interfaces_for_instance()`` found them by iterating every active
NetworkInterface in the cluster and filtering in Python, so the work
done at instance delete, at node startup recovery and in the
deleted-node sweep grew with the whole cluster's interface count
rather than with the instance's (issue 4945). ``Instance.interfaces``
asks the same question with one indexed ``WHERE instance_uuid = ?``.

The helper survived the SQL pushdown work only because it sorted by
``order`` and the property did not say that it did. It does -- the
``ORDER BY`` is in ``_direct_find_network_interfaces()``, which both
the direct and the gRPC paths run -- so the ordering is pinned here as
part of the property's contract.

The helper also keyed a dict on ``ni.order`` while collecting, so two
interfaces of one instance which shared an order silently became one.
That is pinned here too.
"""

from unittest import mock

from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.network.interface import NetworkInterface
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class InstanceInterfacesTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('unittest', 'key1', 'secret')

        self.network = self.mock_mariadb.create_network('net')
        self.instance = self.mock_mariadb.create_instance(name='i1')
        self.other = self.mock_mariadb.create_instance(name='i2')

    def _interface(self, instance, order, address, state=dbo.STATE_CREATED):
        return self.mock_mariadb.create_network_interface(
            netdesc=self.mock_mariadb.generate_netdesc(
                str(self.network.uuid), address=address),
            instance_uuid=str(instance.uuid),
            order=order,
            set_state=state)

    def test_interfaces_are_ordered_by_order(self):
        # Created out of order on purpose: the ordering must come from
        # the query, not from the order the rows were written in.
        for order, address in [(2, '10.9.8.4'), (0, '10.9.8.2'),
                               (1, '10.9.8.3')]:
            self._interface(self.instance, order, address)

        self.assertEqual(
            [0, 1, 2], [ni.order for ni in self.instance.interfaces])

    def test_the_query_is_scoped_to_the_instance(self):
        # The point of the change. One query, carrying this instance's
        # uuid, rather than a read of every interface in the cluster
        # filtered afterwards in Python.
        mine = self._interface(self.instance, 0, '10.9.8.2')
        self._interface(self.other, 0, '10.9.8.3')

        real = mariadb.find_network_interfaces
        criteria = []

        def capture(c):
            criteria.append(c)
            return real(c)

        with mock.patch('shakenfist.mariadb.find_network_interfaces',
                        side_effect=capture):
            nis = self.instance.interfaces

        self.assertEqual([str(mine.uuid)], [str(ni.uuid) for ni in nis])
        self.assertEqual(1, len(criteria))
        self.assertEqual(str(self.instance.uuid), criteria[0].instance_uuid)

    def test_a_deleted_interface_is_not_attached(self):
        # The states the property selects on are the ACTIVE_STATES the
        # retired helper's prefilter resolved to, so a deleted interface
        # stays out of the list.
        self._interface(self.instance, 0, '10.9.8.2')
        gone = self._interface(self.instance, 1, '10.9.8.3')
        gone._state_update(dbo.STATE_DELETED, skip_transition_validation=True)

        self.assertEqual(
            [0], [ni.order for ni in self.instance.interfaces])

    def test_interfaces_sharing_an_order_both_appear(self):
        # The retired helper collected into a dict keyed on ni.order, so
        # the second of these replaced the first and its network was
        # never cleaned up. Nothing stops two rows carrying one order:
        # the uniqueness the table has is on uuid and macaddr.
        first = self._interface(self.instance, 0, '10.9.8.2')
        second = self._interface(self.instance, 0, '10.9.8.3')

        self.assertEqual(
            {str(first.uuid), str(second.uuid)},
            {str(ni.uuid) for ni in self.instance.interfaces})

    def test_an_instance_with_no_interfaces_reads_as_empty(self):
        self._interface(self.other, 0, '10.9.8.3')

        self.assertEqual([], self.instance.interfaces)

    def test_the_cluster_wide_helper_is_gone(self):
        # A reintroduced convenience helper is a reintroduced cluster
        # scan: there is no filtered iteration of NetworkInterfaces
        # which answers this question as cheaply as the indexed query.
        from shakenfist.network import interface as interface_module
        self.assertFalse(hasattr(interface_module, 'interfaces_for_instance'))
        self.assertFalse(hasattr(interface_module, 'instance_filter'))
        self.assertTrue(hasattr(NetworkInterface, 'order'))
