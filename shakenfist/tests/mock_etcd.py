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
from unittest import mock

from shakenfist.instance import Instance
from shakenfist.namespace import Namespace
from shakenfist.network import Network
from shakenfist.networkinterface import NetworkInterface
from shakenfist.node import Node


class MockEtcd():
    """Mock the etcd store with a simple dictionary

    test_obj:   TestCase object
    nodes:      List of node tuples (name, ip, list_of_node_jobs)
    node_count: Number of default nodes. Set node_count or nodes.
    """

    def __init__(self, test_obj, nodes=None, node_count=0):
        self.test_obj = test_obj
        self.db = {}
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
        self.etcd_status = mock.patch('shakenfist.etcd.WrappedEtcdClient.status')
        self.etcd_status.start()
        self.test_obj.addCleanup(self.etcd_status.stop)

        # Mock WrappedEtcdClient()
        self.etcd_create = mock.patch('shakenfist.etcd.WrappedEtcdClient.create',
                                      side_effect=self.create)
        self.etcd_create.start()
        self.test_obj.addCleanup(self.etcd_create.stop)

        self.etcd_get = mock.patch('shakenfist.etcd.WrappedEtcdClient.get',
                                   side_effect=self.get)
        self.etcd_get.start()
        self.test_obj.addCleanup(self.etcd_get.stop)

        self.etcd_get_prefix = mock.patch(
            'shakenfist.etcd.WrappedEtcdClient.get_prefix',
            side_effect=self.get_prefix)
        self.etcd_get_prefix.start()
        self.test_obj.addCleanup(self.etcd_get_prefix.stop)

        self.etcd_put = mock.patch('shakenfist.etcd.WrappedEtcdClient.put',
                                   side_effect=self.put)
        self.etcd_put.start()
        self.test_obj.addCleanup(self.etcd_put.stop)

        self.etcd_delete = mock.patch(
            'shakenfist.etcd.WrappedEtcdClient.delete', side_effect=self.delete)
        self.etcd_delete.start()
        self.test_obj.addCleanup(self.etcd_delete.stop)

        self.etcd_delete_prefix = mock.patch(
            'shakenfist.etcd.WrappedEtcdClient.delete_prefix', side_effect=self.delete_prefix)
        self.etcd_delete_prefix.start()
        self.test_obj.addCleanup(self.etcd_delete_prefix.stop)

        # Mock etcd
        self.etcd_get_lock = mock.patch('shakenfist.etcd.get_lock')
        self.etcd_get_lock.start()
        self.test_obj.addCleanup(self.etcd_get_lock.stop)

        # Setup basic DB data
        for n in self.nodes:
            Node.new(n[0], n[1])

    def next_uuid(self):
        """Generate predictable UUIDs that are unique during the testcase"""
        return '12345678-1234-4321-1234-%012i' % next(self.obj_counter)

    def _trace(self, m):
        if self.emit_tracing:
            print(m)

    #
    # DB operations - Low level
    #

    def create(self, path, encoded, lease=None):
        self.db[path] = encoded
        self._trace(f'MockEtcd.create() {path}: {encoded}')
        return True

    def get(self, path, metadata=False, sort_order=None, sort_target=None):
        d = self.db.get(path)
        self._trace(f'MockEtcd.get() retrieving data for key {path}: {d}')
        if not d:
            return None

        if metadata:
            return [[d]]
        else:
            return [d]

    def get_prefix(self, path, sort_order=None, sort_target=None, limit=0):
        ret = []
        for k in sorted(self.db):
            if k.startswith(path):
                ret.append((self.db[k], {'key': k.encode('utf-8')}))
                self._trace('MockEtcd.delete_prefix() %s' % k)
        return ret

    def put(self, path, encoded, lease=None):
        self.db[path] = encoded
        self._trace(f'MockEtcd.put() {path}: {encoded}')

    def delete(self, path):
        if path in self.db:
            del self.db[path]
            self._trace('MockEtcd.delete() %s' % path)

    def delete_prefix(self, path, sort_order=None, sort_target=None, limit=0):
        for k in sorted(self.db):
            if k.startswith(path):
                del self.db[k]
                self._trace('MockEtcd.delete_prefix() %s' % k)

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
            self.db[key] = json.dumps(data, indent=4, sort_keys=True)

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
