#
# MockEtcd
#
# Mock the Etcd store with a Python dict.
#
import json
import os
import time
from collections import defaultdict
from itertools import count
from typing import Optional
from unittest import mock

from shakenfist.constants import get_object_class
from shakenfist.instance import Instance
from shakenfist.namespace import Namespace
from shakenfist.network.network import Network
from shakenfist.network.interface import NetworkInterface
from shakenfist.node import Node
from shakenfist.schema.dnsmasq import DnsMasqData
from shakenfist.schema.namespace_attributes import NamespaceAttributesData
from shakenfist.schema.namespace_data import NamespaceData
from shakenfist.schema.node_attributes import NodeAttributesData
from shakenfist.schema.node_data import NodeData
from shakenfist.schema.ipam_reservation import IPAMReservation
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.util import json as util_json


class MockEtcd():
    """Mock the etcd store with a simple dictionary

    test_obj:   TestCase object
    nodes:      List of node tuples (name, ip, list_of_node_jobs)
    node_count: Number of default nodes. Set node_count or nodes.
    """

    def __init__(self, test_obj, nodes=None, node_count=0):
        self.test_obj = test_obj
        self.db = {}
        self.mariadb_states = {}  # Mock MariaDB state storage
        self.ipam_reservations = {}  # Mock MariaDB IPAM reservations storage
        self.dnsmasq_objects = {}  # Mock MariaDB DnsMasq object storage
        self.namespace_objects = {}  # Mock MariaDB namespace storage
        self.namespace_attributes = {}  # Mock MariaDB namespace attributes
        self.node_objects = {}  # Mock MariaDB node storage
        self.node_attributes = {}  # Mock MariaDB node attributes
        self.obj_counter = count(1)

        # Define ShakenFist Nodes
        if nodes is not None:
            self.nodes = nodes.copy()
        else:
            # Set default nodes
            assert node_count > 0, 'Must define at least one node'
            self.nodes = [('node1_net', '10.0.0.1', [])]
            for i in range(2, node_count+1):
                self.nodes.append(('node%i' % i, '10.0.0.%i' %
                                   i, ['hypervisor']))

        self.node_names = [n[0] for n in self.nodes]

        # Optional trace logging
        self.emit_tracing = os.environ.get('MOCK_ETCD_TRACE', '0') == '1'

    def setup(self):
        # Mock the health check in get_etcd_client()
        self.etcd_status = mock.patch(
            'shakenfist.etcd.WrappedEtcdClient.status')
        self.etcd_status.start()
        self.test_obj.addCleanup(self.etcd_status.stop)

        # Mock WrappedEtcdClient()
        self.etcd_create_raw = mock.patch(
            'shakenfist.etcd.create_raw',
            side_effect=self.create_raw)
        self.etcd_create_raw.start()
        self.test_obj.addCleanup(self.etcd_create_raw.stop)

        self.etcd_get_raw = mock.patch(
            'shakenfist.etcd.get_raw',
            side_effect=self.get_raw)
        self.etcd_get_raw.start()
        self.test_obj.addCleanup(self.etcd_get_raw.stop)

        self.etcd_get_prefix_raw = mock.patch(
            'shakenfist.etcd.get_prefix_raw',
            side_effect=self.get_prefix_raw)
        self.etcd_get_prefix_raw.start()
        self.test_obj.addCleanup(self.etcd_get_prefix_raw.stop)

        self.etcd_put_raw = mock.patch(
            'shakenfist.etcd.put_raw',
            side_effect=self.put_raw)
        self.etcd_put_raw.start()
        self.test_obj.addCleanup(self.etcd_put_raw.stop)

        self.etcd_delete_raw = mock.patch(
            'shakenfist.etcd.delete_raw',
            side_effect=self.delete_raw)
        self.etcd_delete_raw.start()
        self.test_obj.addCleanup(self.etcd_delete_raw.stop)

        self.etcd_delete_prefix = mock.patch(
            'shakenfist.etcd.WrappedEtcdClient.delete_prefix',
            side_effect=self.delete_prefix)
        self.etcd_delete_prefix.start()
        self.test_obj.addCleanup(self.etcd_delete_prefix.stop)

        self.etcd_replace_many_raw = mock.patch(
            'shakenfist.etcd.replace_many_raw',
            side_effect=self.replace_many_raw)
        self.etcd_replace_many_raw.start()
        self.test_obj.addCleanup(self.etcd_replace_many_raw.stop)

        # Mock MariaDB functions for state storage
        self.mariadb_get_state = mock.patch(
            'shakenfist.mariadb.get_state',
            side_effect=self._mariadb_get_state)
        self.mariadb_get_state.start()
        self.test_obj.addCleanup(self.mariadb_get_state.stop)

        self.mariadb_set_state = mock.patch(
            'shakenfist.mariadb.set_state',
            side_effect=self._mariadb_set_state)
        self.mariadb_set_state.start()
        self.test_obj.addCleanup(self.mariadb_set_state.stop)

        self.mariadb_delete_state = mock.patch(
            'shakenfist.mariadb.delete_state',
            side_effect=self._mariadb_delete_state)
        self.mariadb_delete_state.start()
        self.test_obj.addCleanup(self.mariadb_delete_state.stop)

        self.mariadb_get_objects_by_state = mock.patch(
            'shakenfist.mariadb.get_objects_by_state',
            side_effect=self._mariadb_get_objects_by_state)
        self.mariadb_get_objects_by_state.start()
        self.test_obj.addCleanup(self.mariadb_get_objects_by_state.stop)

        # Mock MariaDB functions for IPAM reservations
        self.mariadb_reserve_address = mock.patch(
            'shakenfist.mariadb.reserve_address',
            side_effect=self._mariadb_reserve_address)
        self.mariadb_reserve_address.start()
        self.test_obj.addCleanup(self.mariadb_reserve_address.stop)

        self.mariadb_release_address = mock.patch(
            'shakenfist.mariadb.release_address',
            side_effect=self._mariadb_release_address)
        self.mariadb_release_address.start()
        self.test_obj.addCleanup(self.mariadb_release_address.stop)

        self.mariadb_get_reservation = mock.patch(
            'shakenfist.mariadb.get_reservation',
            side_effect=self._mariadb_get_reservation)
        self.mariadb_get_reservation.start()
        self.test_obj.addCleanup(self.mariadb_get_reservation.stop)

        self.mariadb_get_reservations_for_ipam = mock.patch(
            'shakenfist.mariadb.get_reservations_for_ipam',
            side_effect=self._mariadb_get_reservations_for_ipam)
        self.mariadb_get_reservations_for_ipam.start()
        self.test_obj.addCleanup(self.mariadb_get_reservations_for_ipam.stop)

        self.mariadb_delete_reservation = mock.patch(
            'shakenfist.mariadb.delete_reservation',
            side_effect=self._mariadb_delete_reservation)
        self.mariadb_delete_reservation.start()
        self.test_obj.addCleanup(self.mariadb_delete_reservation.stop)

        self.mariadb_delete_reservations_for_ipam = mock.patch(
            'shakenfist.mariadb.delete_reservations_for_ipam',
            side_effect=self._mariadb_delete_reservations_for_ipam)
        self.mariadb_delete_reservations_for_ipam.start()
        self.test_obj.addCleanup(self.mariadb_delete_reservations_for_ipam.stop)

        self.mariadb_release_haloed_addresses = mock.patch(
            'shakenfist.mariadb.release_haloed_addresses',
            side_effect=self._mariadb_release_haloed_addresses)
        self.mariadb_release_haloed_addresses.start()
        self.test_obj.addCleanup(self.mariadb_release_haloed_addresses.stop)

        self.mariadb_get_addresses_in_use = mock.patch(
            'shakenfist.mariadb.get_addresses_in_use',
            side_effect=self._mariadb_get_addresses_in_use)
        self.mariadb_get_addresses_in_use.start()
        self.test_obj.addCleanup(self.mariadb_get_addresses_in_use.stop)

        # Mock MariaDB DnsMasq operations
        self.mariadb_create_dnsmasq = mock.patch(
            'shakenfist.mariadb.create_dnsmasq',
            side_effect=self._mariadb_create_dnsmasq)
        self.mariadb_create_dnsmasq.start()
        self.test_obj.addCleanup(self.mariadb_create_dnsmasq.stop)

        self.mariadb_get_dnsmasq = mock.patch(
            'shakenfist.mariadb.get_dnsmasq',
            side_effect=self._mariadb_get_dnsmasq)
        self.mariadb_get_dnsmasq.start()
        self.test_obj.addCleanup(self.mariadb_get_dnsmasq.stop)

        self.mariadb_get_dnsmasqs = mock.patch(
            'shakenfist.mariadb.get_dnsmasqs',
            side_effect=self._mariadb_get_dnsmasqs)
        self.mariadb_get_dnsmasqs.start()
        self.test_obj.addCleanup(self.mariadb_get_dnsmasqs.stop)

        self.mariadb_delete_dnsmasq = mock.patch(
            'shakenfist.mariadb.delete_dnsmasq',
            side_effect=self._mariadb_delete_dnsmasq)
        self.mariadb_delete_dnsmasq.start()
        self.test_obj.addCleanup(self.mariadb_delete_dnsmasq.stop)

        self.mariadb_update_dnsmasq = mock.patch(
            'shakenfist.mariadb.update_dnsmasq',
            side_effect=self._mariadb_update_dnsmasq)
        self.mariadb_update_dnsmasq.start()
        self.test_obj.addCleanup(self.mariadb_update_dnsmasq.stop)

        # Mock MariaDB ObjectReference operations
        self.mariadb_get_references_to = mock.patch(
            'shakenfist.mariadb.get_references_to',
            side_effect=self._mariadb_get_references_to)
        self.mariadb_get_references_to.start()
        self.test_obj.addCleanup(self.mariadb_get_references_to.stop)

        self.mariadb_get_references_from = mock.patch(
            'shakenfist.mariadb.get_references_from',
            side_effect=self._mariadb_get_references_from)
        self.mariadb_get_references_from.start()
        self.test_obj.addCleanup(self.mariadb_get_references_from.stop)

        self.mariadb_remove_all_references_from = mock.patch(
            'shakenfist.mariadb.remove_all_references_from',
            side_effect=self._mariadb_remove_all_references_from)
        self.mariadb_remove_all_references_from.start()
        self.test_obj.addCleanup(self.mariadb_remove_all_references_from.stop)

        # Mock MariaDB Node operations
        self.mariadb_create_node = mock.patch(
            'shakenfist.mariadb.create_node',
            side_effect=self._mariadb_create_node)
        self.mariadb_create_node.start()
        self.test_obj.addCleanup(
            self.mariadb_create_node.stop)

        self.mariadb_get_node = mock.patch(
            'shakenfist.mariadb.get_node',
            side_effect=self._mariadb_get_node)
        self.mariadb_get_node.start()
        self.test_obj.addCleanup(
            self.mariadb_get_node.stop)

        self.mariadb_get_node_by_fqdn = mock.patch(
            'shakenfist.mariadb.get_node_by_fqdn',
            side_effect=self._mariadb_get_node_by_fqdn)
        self.mariadb_get_node_by_fqdn.start()
        self.test_obj.addCleanup(
            self.mariadb_get_node_by_fqdn.stop)

        self.mariadb_get_all_node_uuids = mock.patch(
            'shakenfist.mariadb.get_all_node_uuids',
            side_effect=self._mariadb_get_all_node_uuids)
        self.mariadb_get_all_node_uuids.start()
        self.test_obj.addCleanup(
            self.mariadb_get_all_node_uuids.stop)

        self.mariadb_update_node = mock.patch(
            'shakenfist.mariadb.update_node',
            side_effect=self._mariadb_update_node)
        self.mariadb_update_node.start()
        self.test_obj.addCleanup(
            self.mariadb_update_node.stop)

        self.mariadb_delete_node = mock.patch(
            'shakenfist.mariadb.delete_node',
            side_effect=self._mariadb_delete_node)
        self.mariadb_delete_node.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_node.stop)

        self.mariadb_create_node_attributes = mock.patch(
            'shakenfist.mariadb.create_node_attributes',
            side_effect=(
                self._mariadb_create_node_attributes))
        self.mariadb_create_node_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_create_node_attributes.stop)

        self.mariadb_get_node_attributes = mock.patch(
            'shakenfist.mariadb.get_node_attributes',
            side_effect=(
                self._mariadb_get_node_attributes))
        self.mariadb_get_node_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_get_node_attributes.stop)

        self.mariadb_update_node_attributes = mock.patch(
            'shakenfist.mariadb.update_node_attributes',
            side_effect=(
                self._mariadb_update_node_attributes))
        self.mariadb_update_node_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_update_node_attributes.stop)

        self.mariadb_delete_node_attributes = mock.patch(
            'shakenfist.mariadb.delete_node_attributes',
            side_effect=(
                self._mariadb_delete_node_attributes))
        self.mariadb_delete_node_attributes.start()
        self.test_obj.addCleanup(
            self.mariadb_delete_node_attributes.stop)

        # Mock MariaDB Namespace operations
        self.mariadb_create_namespace = mock.patch(
            'shakenfist.mariadb.create_namespace',
            side_effect=self._mariadb_create_namespace)
        self.mariadb_create_namespace.start()
        self.test_obj.addCleanup(self.mariadb_create_namespace.stop)

        self.mariadb_get_namespace = mock.patch(
            'shakenfist.mariadb.get_namespace',
            side_effect=self._mariadb_get_namespace)
        self.mariadb_get_namespace.start()
        self.test_obj.addCleanup(self.mariadb_get_namespace.stop)

        self.mariadb_get_all_namespace_names = mock.patch(
            'shakenfist.mariadb.get_all_namespace_names',
            side_effect=self._mariadb_get_all_namespace_names)
        self.mariadb_get_all_namespace_names.start()
        self.test_obj.addCleanup(self.mariadb_get_all_namespace_names.stop)

        self.mariadb_delete_namespace = mock.patch(
            'shakenfist.mariadb.delete_namespace',
            side_effect=self._mariadb_delete_namespace)
        self.mariadb_delete_namespace.start()
        self.test_obj.addCleanup(self.mariadb_delete_namespace.stop)

        self.mariadb_create_namespace_attributes = mock.patch(
            'shakenfist.mariadb.create_namespace_attributes',
            side_effect=self._mariadb_create_namespace_attributes)
        self.mariadb_create_namespace_attributes.start()
        self.test_obj.addCleanup(self.mariadb_create_namespace_attributes.stop)

        self.mariadb_get_namespace_attributes = mock.patch(
            'shakenfist.mariadb.get_namespace_attributes',
            side_effect=self._mariadb_get_namespace_attributes)
        self.mariadb_get_namespace_attributes.start()
        self.test_obj.addCleanup(self.mariadb_get_namespace_attributes.stop)

        self.mariadb_update_namespace_attributes = mock.patch(
            'shakenfist.mariadb.update_namespace_attributes',
            side_effect=self._mariadb_update_namespace_attributes)
        self.mariadb_update_namespace_attributes.start()
        self.test_obj.addCleanup(self.mariadb_update_namespace_attributes.stop)

        self.mariadb_delete_namespace_attributes = mock.patch(
            'shakenfist.mariadb.delete_namespace_attributes',
            side_effect=self._mariadb_delete_namespace_attributes)
        self.mariadb_delete_namespace_attributes.start()
        self.test_obj.addCleanup(self.mariadb_delete_namespace_attributes.stop)

        # Setup basic DB data
        for n in self.nodes:
            Node.new(n[0], n[1])

    def next_uuid(self):
        """Generate predictable UUIDs that are unique during the testcase"""
        # NOTE(mikal): there are version and variant fields in uuid4's that
        # pydantic enforces.
        #               version    variant
        #                     *    *
        return '12345678-1234-4321-8234-%012i' % next(self.obj_counter)

    def _trace(self, m):
        if self.emit_tracing:
            print(m)

    #
    # MariaDB mock operations
    #

    def _mariadb_get_state(self, object_type: ObjectType,
                           object_uuid: str) -> Optional[State]:
        """Mock implementation of mariadb.get_state()"""
        # Key by object_type and object_uuid to avoid collisions between
        # different object types sharing the same UUID (e.g., ipam/network)
        key = f'{object_type}/{object_uuid}'
        if key in self.mariadb_states:
            data = self.mariadb_states[key]
            self._trace(f'MockMariaDB.get_state({key}): {data}')
            return State(
                value=data['state_value'],
                update_time=data['update_time'],
                message=data['message']
            )
        self._trace(f'MockMariaDB.get_state({key}): None')
        return None

    def _mariadb_set_state(self, object_type: ObjectType, object_uuid: str,
                           state: State) -> bool:
        """Mock implementation of mariadb.set_state()"""
        key = f'{object_type}/{object_uuid}'
        self.mariadb_states[key] = {
            'object_type': object_type,
            'object_uuid': object_uuid,
            'state_value': state.value,
            'update_time': state.update_time,
            'message': state.message
        }
        self._trace(
            f'MockMariaDB.set_state({key}): {state.value}')
        return True

    def _mariadb_delete_state(self, object_type: ObjectType,
                              object_uuid: str) -> bool:
        """Mock implementation of mariadb.delete_state()"""
        key = f'{object_type}/{object_uuid}'
        if key in self.mariadb_states:
            del self.mariadb_states[key]
            self._trace(f'MockMariaDB.delete_state({key}): deleted')
        else:
            self._trace(f'MockMariaDB.delete_state({key}): not found')
        return True

    def _mariadb_get_objects_by_state(self, object_type: ObjectType,
                                      state_values: list[str]) -> list[str]:
        """Mock implementation of mariadb.get_objects_by_state()"""
        result = []
        for key, data in self.mariadb_states.items():
            if (data['object_type'] == object_type and
                    data['state_value'] in state_values):
                result.append(data['object_uuid'])
        self._trace(
            f'MockMariaDB.get_objects_by_state({object_type}, '
            f'{state_values}): {result}')
        return result

    def get_mariadb_state(self, object_type: ObjectType,
                          object_uuid: str) -> Optional[dict]:
        """Get state from the mock MariaDB store for test assertions.

        Returns a dict with 'value' and 'update_time' keys, matching the format
        previously used in etcd, or None if no state exists.
        """
        key = f'{object_type}/{object_uuid}'
        if key in self.mariadb_states:
            data = self.mariadb_states[key]
            return {
                'value': data['state_value'],
                'update_time': data['update_time']
            }
        return None

    #
    # MariaDB IPAM mock operations
    #

    def _ipam_key(self, ipam_uuid: str, address: str) -> str:
        """Generate a unique key for an IPAM reservation.

        The address can be either a string or an IPv4Address object.
        """
        return f'{ipam_uuid}/{address}'

    def _mariadb_reserve_address(self, reservation: IPAMReservation) -> bool:
        """Mock implementation of mariadb.reserve_address()"""
        key = self._ipam_key(reservation.ipam_uuid, str(reservation.address))
        if key in self.ipam_reservations:
            self._trace(f'MockMariaDB.reserve_address({key}): already exists')
            return False
        self.ipam_reservations[key] = reservation
        self._trace(f'MockMariaDB.reserve_address({key}): success')
        return True

    def _mariadb_release_address(self, ipam_uuid: str, address: str,
                                 halo_reservation: IPAMReservation) -> bool:
        """Mock implementation of mariadb.release_address()"""
        key = self._ipam_key(ipam_uuid, address)
        if key not in self.ipam_reservations:
            self._trace(f'MockMariaDB.release_address({key}): not found')
            return False
        self.ipam_reservations[key] = halo_reservation
        self._trace(f'MockMariaDB.release_address({key}): updated to halo')
        return True

    def _mariadb_get_reservation(self, ipam_uuid: str,
                                 address: str) -> Optional[IPAMReservation]:
        """Mock implementation of mariadb.get_reservation()"""
        key = self._ipam_key(ipam_uuid, address)
        reservation = self.ipam_reservations.get(key)
        self._trace(f'MockMariaDB.get_reservation({key}): {reservation}')
        return reservation

    def _mariadb_get_reservations_for_ipam(
            self, ipam_uuid: str) -> list[IPAMReservation]:
        """Mock implementation of mariadb.get_reservations_for_ipam()"""
        result = []
        prefix = f'{ipam_uuid}/'
        for key, reservation in self.ipam_reservations.items():
            if key.startswith(prefix):
                result.append(reservation)
        self._trace(
            f'MockMariaDB.get_reservations_for_ipam({ipam_uuid}): '
            f'{len(result)} reservations')
        return result

    def _mariadb_delete_reservation(self, ipam_uuid: str, address: str) -> bool:
        """Mock implementation of mariadb.delete_reservation()"""
        key = self._ipam_key(ipam_uuid, address)
        if key in self.ipam_reservations:
            del self.ipam_reservations[key]
            self._trace(f'MockMariaDB.delete_reservation({key}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_reservation({key}): not found')
        return False

    def _mariadb_delete_reservations_for_ipam(self, ipam_uuid: str) -> int:
        """Mock implementation of mariadb.delete_reservations_for_ipam()"""
        prefix = f'{ipam_uuid}/'
        to_delete = [k for k in self.ipam_reservations if k.startswith(prefix)]
        for key in to_delete:
            del self.ipam_reservations[key]
        self._trace(
            f'MockMariaDB.delete_reservations_for_ipam({ipam_uuid}): '
            f'deleted {len(to_delete)}')
        return len(to_delete)

    def _mariadb_release_haloed_addresses(self, ipam_uuid: str,
                                          older_than: float) -> int:
        """Mock implementation of mariadb.release_haloed_addresses()"""
        from shakenfist.schema.ipam_reservation import ReservationType

        prefix = f'{ipam_uuid}/'
        to_delete = []
        for key, reservation in self.ipam_reservations.items():
            if (key.startswith(prefix) and
                    reservation.reservation_type == ReservationType.DELETION_HALO
                    and reservation.reserved_at < older_than):
                to_delete.append(key)
        for key in to_delete:
            del self.ipam_reservations[key]
        self._trace(
            f'MockMariaDB.release_haloed_addresses({ipam_uuid}, '
            f'{older_than}): deleted {len(to_delete)}')
        return len(to_delete)

    def _mariadb_get_addresses_in_use(self, ipam_uuid: str) -> set[str]:
        """Mock implementation of mariadb.get_addresses_in_use()"""
        result = set()
        prefix = f'{ipam_uuid}/'
        for key in self.ipam_reservations:
            if key.startswith(prefix):
                # Extract address from key (format: ipam_uuid/address)
                address = key[len(prefix):]
                result.add(address)
        self._trace(
            f'MockMariaDB.get_addresses_in_use({ipam_uuid}): {len(result)} '
            f'addresses')
        return result

    def _mariadb_create_dnsmasq(self, data: DnsMasqData) -> bool:
        """Mock implementation of mariadb.create_dnsmasq()"""
        key = str(data.uuid)
        if key in self.dnsmasq_objects:
            self._trace(f'MockMariaDB.create_dnsmasq({key}): already exists')
            return False
        self.dnsmasq_objects[key] = data
        self._trace(f'MockMariaDB.create_dnsmasq({key}): created')
        return True

    def _mariadb_get_dnsmasq(self, dnsmasq_uuid) -> Optional[DnsMasqData]:
        """Mock implementation of mariadb.get_dnsmasq()"""
        key = str(dnsmasq_uuid)
        data = self.dnsmasq_objects.get(key)
        self._trace(f'MockMariaDB.get_dnsmasq({key}): {data}')
        return data

    def _mariadb_get_dnsmasqs(
            self, namespace: Optional[str] = None,
            owner_uuid=None) -> list[DnsMasqData]:
        """Mock implementation of mariadb.get_dnsmasqs()"""
        result = []
        for data in self.dnsmasq_objects.values():
            if namespace and data.namespace != namespace:
                continue
            if owner_uuid and str(data.owner_uuid) != str(owner_uuid):
                continue
            result.append(data)
        self._trace(
            f'MockMariaDB.get_dnsmasqs(namespace={namespace}, '
            f'owner_uuid={owner_uuid}): {len(result)}')
        return result

    def _mariadb_delete_dnsmasq(self, dnsmasq_uuid) -> bool:
        """Mock implementation of mariadb.delete_dnsmasq()"""
        key = str(dnsmasq_uuid)
        if key in self.dnsmasq_objects:
            del self.dnsmasq_objects[key]
            self._trace(f'MockMariaDB.delete_dnsmasq({key}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_dnsmasq({key}): not found')
        return False

    def _mariadb_update_dnsmasq(self, data: DnsMasqData) -> bool:
        """Mock implementation of mariadb.update_dnsmasq()"""
        key = str(data.uuid)
        if key in self.dnsmasq_objects:
            self.dnsmasq_objects[key] = data
            self._trace(f'MockMariaDB.update_dnsmasq({key}): updated')
            return True
        self._trace(f'MockMariaDB.update_dnsmasq({key}): not found')
        return False

    #
    # MariaDB ObjectReference mock operations
    #

    def _mariadb_get_references_to(self, object_type: ObjectType,
                                   object_uuid: str) -> list:
        """Mock implementation of mariadb.get_references_to()

        Returns an empty list since tests don't typically need actual
        reference data.
        """
        self._trace(
            f'MockMariaDB.get_references_to({object_type}, {object_uuid}): []')
        return []

    def _mariadb_get_references_from(self, object_type: ObjectType,
                                     object_uuid: str) -> list:
        """Mock implementation of mariadb.get_references_from()

        Returns an empty list since tests don't typically need actual
        reference data.
        """
        self._trace(
            f'MockMariaDB.get_references_from({object_type}, {object_uuid}): '
            '[]')
        return []

    def _mariadb_remove_all_references_from(self, object_type: ObjectType,
                                            object_uuid: str) -> int:
        """Mock implementation of mariadb.remove_all_references_from()

        Returns 0 since tests don't typically have actual reference data.
        """
        self._trace(
            f'MockMariaDB.remove_all_references_from({object_type}, '
            f'{object_uuid}): 0')
        return 0

    #
    # MariaDB Node mock operations
    #

    def _mariadb_create_node(self, node_uuid, fqdn,
                             ip, version) -> bool:
        """Mock implementation of mariadb.create_node()"""
        import uuid as uuid_mod
        key = str(node_uuid)
        if key in self.node_objects:
            self._trace(
                f'MockMariaDB.create_node({key}): exists')
            return False
        if isinstance(node_uuid, str):
            node_uuid = uuid_mod.UUID(node_uuid)
        data = NodeData(
            uuid=node_uuid, fqdn=fqdn,
            ip=ip, version=version)
        self.node_objects[key] = data
        self._trace(
            f'MockMariaDB.create_node({key}): created')
        return True

    def _mariadb_get_node(self, node_uuid
                          ) -> Optional[NodeData]:
        """Mock implementation of mariadb.get_node()"""
        key = str(node_uuid)
        data = self.node_objects.get(key)
        self._trace(
            f'MockMariaDB.get_node({key}): {data}')
        return data

    def _mariadb_get_node_by_fqdn(
            self, fqdn) -> Optional[NodeData]:
        """Mock implementation of
        mariadb.get_node_by_fqdn()"""
        for data in self.node_objects.values():
            if data.fqdn == fqdn:
                self._trace(
                    f'MockMariaDB.get_node_by_fqdn'
                    f'({fqdn}): {data}')
                return data
        self._trace(
            f'MockMariaDB.get_node_by_fqdn'
            f'({fqdn}): None')
        return None

    def _mariadb_get_all_node_uuids(self) -> list[str]:
        """Mock implementation of
        mariadb.get_all_node_uuids()"""
        result = list(self.node_objects.keys())
        self._trace(
            f'MockMariaDB.get_all_node_uuids(): '
            f'{result}')
        return result

    def _mariadb_update_node(self, data: NodeData
                             ) -> bool:
        """Mock implementation of
        mariadb.update_node()"""
        key = str(data.uuid)
        if key in self.node_objects:
            self.node_objects[key] = data
            self._trace(
                f'MockMariaDB.update_node({key}): '
                f'updated')
            return True
        self._trace(
            f'MockMariaDB.update_node({key}): '
            f'not found')
        return False

    def _mariadb_delete_node(self, node_uuid) -> bool:
        """Mock implementation of
        mariadb.delete_node()"""
        key = str(node_uuid)
        if key in self.node_objects:
            del self.node_objects[key]
            self._trace(
                f'MockMariaDB.delete_node({key}): '
                f'deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_node({key}): '
            f'not found')
        return False

    def _mariadb_create_node_attributes(
            self, data: NodeAttributesData) -> bool:
        """Mock implementation of
        mariadb.create_node_attributes()"""
        key = str(data.uuid)
        if key in self.node_attributes:
            self._trace(
                f'MockMariaDB.create_node_attributes'
                f'({key}): exists')
            return False
        self.node_attributes[key] = data
        self._trace(
            f'MockMariaDB.create_node_attributes'
            f'({key}): created')
        return True

    def _mariadb_get_node_attributes(
            self, node_uuid
    ) -> Optional[NodeAttributesData]:
        """Mock implementation of
        mariadb.get_node_attributes()"""
        key = str(node_uuid)
        data = self.node_attributes.get(key)
        self._trace(
            f'MockMariaDB.get_node_attributes'
            f'({key}): {data}')
        return data

    def _mariadb_update_node_attributes(
            self, data: NodeAttributesData) -> bool:
        """Mock implementation of
        mariadb.update_node_attributes()"""
        key = str(data.uuid)
        if key in self.node_attributes:
            self.node_attributes[key] = data
            self._trace(
                f'MockMariaDB.update_node_attributes'
                f'({key}): updated')
            return True
        self._trace(
            f'MockMariaDB.update_node_attributes'
            f'({key}): not found')
        return False

    def _mariadb_delete_node_attributes(
            self, node_uuid) -> bool:
        """Mock implementation of
        mariadb.delete_node_attributes()"""
        key = str(node_uuid)
        if key in self.node_attributes:
            del self.node_attributes[key]
            self._trace(
                f'MockMariaDB.delete_node_attributes'
                f'({key}): deleted')
            return True
        self._trace(
            f'MockMariaDB.delete_node_attributes'
            f'({key}): not found')
        return False

    #
    # MariaDB Namespace mock operations
    #

    def _mariadb_create_namespace(self, name, version) -> bool:
        """Mock implementation of mariadb.create_namespace()"""
        if name in self.namespace_objects:
            self._trace(f'MockMariaDB.create_namespace({name}): exists')
            return False
        data = NamespaceData(name=name, version=version)
        self.namespace_objects[name] = data
        self._trace(f'MockMariaDB.create_namespace({name}): created')
        return True

    def _mariadb_get_namespace(self, name) -> Optional[NamespaceData]:
        """Mock implementation of mariadb.get_namespace()"""
        data = self.namespace_objects.get(name)
        self._trace(f'MockMariaDB.get_namespace({name}): {data}')
        return data

    def _mariadb_get_all_namespace_names(self) -> list[str]:
        """Mock implementation of mariadb.get_all_namespace_names()"""
        result = sorted(self.namespace_objects.keys())
        self._trace(f'MockMariaDB.get_all_namespace_names(): {result}')
        return result

    def _mariadb_delete_namespace(self, name) -> bool:
        """Mock implementation of mariadb.delete_namespace()"""
        if name in self.namespace_objects:
            del self.namespace_objects[name]
            self._trace(f'MockMariaDB.delete_namespace({name}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_namespace({name}): not found')
        return False

    def _mariadb_create_namespace_attributes(self, data: NamespaceAttributesData) -> bool:
        """Mock implementation of mariadb.create_namespace_attributes()"""
        if data.name in self.namespace_attributes:
            self._trace(f'MockMariaDB.create_namespace_attributes({data.name}): exists')
            return False
        self.namespace_attributes[data.name] = data
        self._trace(f'MockMariaDB.create_namespace_attributes({data.name}): created')
        return True

    def _mariadb_get_namespace_attributes(self, name) -> Optional[NamespaceAttributesData]:
        """Mock implementation of mariadb.get_namespace_attributes()"""
        data = self.namespace_attributes.get(name)
        self._trace(f'MockMariaDB.get_namespace_attributes({name}): {data}')
        return data

    def _mariadb_update_namespace_attributes(self, data: NamespaceAttributesData) -> bool:
        """Mock implementation of mariadb.update_namespace_attributes()"""
        if data.name in self.namespace_attributes:
            self.namespace_attributes[data.name] = data
            self._trace(f'MockMariaDB.update_namespace_attributes({data.name}): updated')
            return True
        self._trace(f'MockMariaDB.update_namespace_attributes({data.name}): not found')
        return False

    def _mariadb_delete_namespace_attributes(self, name) -> bool:
        """Mock implementation of mariadb.delete_namespace_attributes()"""
        if name in self.namespace_attributes:
            del self.namespace_attributes[name]
            self._trace(f'MockMariaDB.delete_namespace_attributes({name}): deleted')
            return True
        self._trace(f'MockMariaDB.delete_namespace_attributes({name}): not found')
        return False

    #
    # DB operations - Low level
    #

    def get_prefix_raw(self, path, limit=0):
        ret = []
        for k in sorted(self.db):
            if k.startswith(path):
                d = json.loads(self.db[k].decode())
                ret.append((k, d))
                self._trace(
                    f'MockEtcd.get_prefix_raw({path}) included key {k}: {d}')

            if limit > 0 and len(ret) == limit:
                return ret
        return ret

    def delete_raw(self, path):
        if path in self.db:
            del self.db[path]
            self._trace('MockEtcd.delete() %s' % path)

    def delete_prefix(self, path, sort_order=None, sort_target=None, limit=0):
        for k in sorted(self.db):
            if k.startswith(path):
                del self.db[k]
                self._trace('MockEtcd.delete_prefix() %s' % k)

    # Newer gRPC methods
    def create_raw(self, path, data, lease=None):
        if path not in self.db:
            self.db[path] = util_json.json_dump(data).encode()
            self._trace(f'MockEtcd.create() {path} successful')
            return True

        self._trace(f'MockEtcd.create() {path} failure')
        return False

    def get_raw(self, path):
        d = self.db.get(path)
        if d:
            d = json.loads(d.decode())
        self._trace(f'MockEtcd.get() retrieving data for key {path}: {d}')
        return d

    def put_raw(self, path, data, lease=None):
        encoded = util_json.json_dump(data).encode()
        self.db[path] = encoded
        self._trace(f'MockEtcd.put() {path}: {encoded}')

    def replace_many_raw(self, mutations):
        updates = {}
        deletes = []
        failures = []

        for mutation in mutations:
            path = mutation['path']
            ode = util_json.json_dump(mutation['original_data']).encode()
            nde = util_json.json_dump(mutation['new_data']).encode()

            if not mutation['original_data']:
                if path in self.db:
                    self._trace(f'MockEtcd.replace_many_raw() {path} failure: '
                                'path exists')
                    failures.append(
                        {
                            'path': path,
                            'desired': None,
                            'actual': self.db[path].decode()
                        }
                    )
                elif mutation['new_data']:
                    updates[path] = nde
                elif path in updates:
                    del updates[path]
            else:
                if path not in self.db:
                    self._trace(f'MockEtcd.replace_many_raw() {path} failure: '
                                'path does not exist')
                    failures.append(
                        {
                            'path': path,
                            'desired': mutation['original_data'],
                            'actual': None
                        }
                    )
                if self.db[path] != ode:
                    self._trace(f'MockEtcd.replace_many_raw() {path} failure: '
                                f'{self.db[path]} != {ode}')
                    failures.append(
                        {
                            'path': path,
                            'desired': mutation['original_data'],
                            'actual': self.db[path]
                        }
                    )

                if not mutation['new_data']:
                    deletes.append(path)
                else:
                    updates[path] = nde

        if failures:
            return False, failures

        self.db.update(updates)
        for path in deletes:
            del self.db[path]
        self._trace('MockEtcd.replace_many_raw() success')
        return True, []

    #
    # DB operations - Utilizing SF DB functionality
    #

    def set_node_metrics_same(self, metrics=None):
        if not metrics:
            metrics = {
                'cpu_max_per_instance': 16,
                'cpu_max': 4,
                'memory_available': 22000,
                'memory_max': 24000,
                'disk_free_instances': 2000*1024*1024*1024,
                'cpu_total_instance_vcpus': 4,
                'cpu_available': 12,
            }

        for n in self.nodes:
            key = '/sf/metrics/%s/' % n[0]
            metrics['is_hypervisor'] = 'hypervisor' in n[2]
            data = {
                'fqdn': n[0],
                'timestamp': time.time(),
                'metrics': metrics,
            }
            self.db[key] = util_json.json_dump(data).encode()

    #
    # Database backed objects
    #

    def create_namespace(self, namespace, key_name, key):
        ns = Namespace.new(namespace)
        ns.add_key(key_name, key)

    def create_instance(self, name,
                        uuid=None,
                        cpus=1,
                        disk_spec=[{'base': 'cirros', 'size': 21}],
                        memory=1024,
                        namespace='unittest',
                        requested_placement='',
                        ssh_key='ssh-rsa AAAAB3Nabc unit@test',
                        user_data='',
                        video='cirrus',
                        uefi=False,
                        configdrive='openstack-disk',
                        metadata=None,
                        set_state=Instance.STATE_CREATED,
                        place_on_node='',
                        ):

        if not uuid:
            uuid = self.next_uuid()

        inst = Instance.new(name=name,
                            cpus=cpus,
                            memory=memory,
                            namespace=namespace,
                            ssh_key=ssh_key,
                            disk_spec=disk_spec,
                            user_data=user_data,
                            video=video,
                            requested_placement=requested_placement,
                            instance_uuid=uuid,
                            uefi=uefi,
                            configdrive=configdrive,
                            )

        if metadata:
            inst._db_set_attribute('metadata', metadata)

        # We just smash the requested state into the object, we don't attempt
        # to find a valid path to that state.
        inst._state_update(set_state, skip_transition_validation=True)

        if place_on_node:
            inst.place_instance(place_on_node)

        return inst

    def create_network(self, name,
                       uuid=None,
                       namespace='unittest',
                       netblock='10.9.8.0/24',
                       provide_dhcp=False,
                       provide_nat=False,
                       provide_dns=False,
                       vxid=None,
                       metadata=None,
                       set_state=Network.STATE_CREATED,
                       ):

        if not uuid:
            uuid = self.next_uuid()

        network = Network.new(name=name,
                              namespace=namespace,
                              netblock=netblock,
                              provide_dhcp=provide_dhcp,
                              provide_nat=provide_nat,
                              provide_dns=provide_dns,
                              network_uuid=uuid,
                              vxid=vxid,
                              )

        if metadata:
            network._db_set_attribute('metadata', metadata)

        state_path = defaultdict(set)
        for initial, allowed in Network.state_targets.items():
            if allowed:
                for a in allowed:
                    state_path[a].add(initial)

        # We just smash the requested state into the object, we don't attempt
        # to find a valid path to that state.
        network._state_update(set_state, skip_transition_validation=True)

        # Ignore cluster operations because we don't do them in unit tests.
        # Use skip_transition_validation since operations may start in
        # various states depending on how they were created.
        last_op = network.last_cluster_operation
        if last_op and last_op.get('op_type'):
            op = get_object_class(last_op.get('op_type')).from_db(
                last_op.get('op_uuid'))
            op._state_update(op.STATE_EXECUTING, skip_transition_validation=True)
            op._state_update(op.STATE_COMPLETE, skip_transition_validation=True)

        return network

    def generate_netdesc(self,
                         network_uuid,
                         address='10.1.2.3',
                         model='virtio',
                         mac_address=None):
        return {
            'network_uuid': network_uuid,
            'address': address,
            'model': model,
            'macaddress': mac_address,
        }

    def create_network_interface(self,
                                 uuid=None,
                                 netdesc=None,
                                 instance_uuid=None,
                                 order=1,
                                 set_state=Network.STATE_CREATED
                                 ):

        # Handle default test data
        if not netdesc:
            raise Exception('Must set netdesc (use generate_netdesc()')

        net_iface = NetworkInterface.new(uuid, netdesc, instance_uuid, order)

        state_path = defaultdict(set)
        for initial, allowed in NetworkInterface.state_targets.items():
            if allowed:
                for a in allowed:
                    state_path[a].add(initial)

        # We just smash the requested state into the object, we don't attempt
        # to find a valid path to that state.
        net_iface._state_update(set_state, skip_transition_validation=True)

        return net_iface
