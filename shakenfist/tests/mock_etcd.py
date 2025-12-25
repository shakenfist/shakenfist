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
        """Generate a unique key for an IPAM reservation."""
        return f'{ipam_uuid}/{address}'

    def _mariadb_reserve_address(self, reservation: IPAMReservation) -> bool:
        """Mock implementation of mariadb.reserve_address()"""
        key = self._ipam_key(reservation.ipam_uuid, reservation.address)
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
