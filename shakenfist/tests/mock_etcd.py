#
# MockEtcd
#
# Mock the Etcd store with a Python dict.
#

import base64
import bcrypt
from collections import defaultdict
from itertools import count
import json
import mock
import time

from shakenfist import db
from shakenfist.baseobject import DatabaseBackedObject
from shakenfist.instance import Instance
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

    def setup(self):
        # Mock WrappedEtcdClient
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

    #
    # DB operations - Low level
    #

    def create(self, path, encoded, lease=None):
        self.db[path] = encoded
        print('MockEtcd.create() %s: %s' % (path, encoded))
        return True

    def get(self, path, metadata=False, sort_order=None, sort_target=None):
        d = self.db.get(path)
        print('MockEtcd.get() retrieving data for key %s: %s' % (path, d))
        if not d:
            return None
        return [[d]]

    def get_prefix(self, path, sort_order=None, sort_target=None, limit=0):
        ret = []
        for k in sorted(self.db):
            if k.startswith(path):
                ret.append((self.db[k], {'key': k.encode('utf-8')}))
        return ret

    def put(self, path, encoded, lease=None):
        self.db[path] = encoded
        print('MockEtcd.put() %s: %s' % (path, encoded))

    #
    # DB operations - Utilising SF DB functionality
    #

    def persist_metadata(self, dbo_type, uuid, metadata):
        """Set metadata for a specified object"""
        if not DatabaseBackedObject.__subclasscheck__(dbo_type):
            raise NotImplementedError(
                'Objects must be subclasses of DatabaseBackedObject')
        db.persist_metadata(dbo_type.object_type, uuid, metadata)

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
        encoded = str(base64.b64encode(bcrypt.hashpw(
                      key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
        rec = {
            'name': namespace,
            'keys': {
                key_name: encoded
            }
        }
        db.persist_metadata('namespace', namespace, {})
        db.persist_namespace(namespace, rec)

    @staticmethod
    def _find_start(obj, state_path, initial, dest):
        for s in state_path[dest]:
            if initial == s:
                return True
            if MockEtcd._find_start(obj, state_path, initial, s):
                obj.state = s
                return True
        return False

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
                        metadata={},
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
        self.persist_metadata(Instance, inst.uuid, metadata)

        state_path = defaultdict(set)
        for initial, allowed in Instance.state_targets.items():
            if allowed:
                for a in allowed:
                    state_path[a].add(initial)

        self._find_start(inst, state_path, Instance.STATE_INITIAL, set_state)
        inst.state = set_state

        if place_on_node:
            inst.place_instance(place_on_node)

        return inst

    def create_network(self, name,
                       uuid=None,
                       namespace='unittest',
                       netblock='10.9.8.0/24',
                       provide_dhcp=False,
                       provide_nat=False,
                       vxid=None,
                       metadata={},
                       set_state=Network.STATE_CREATED,
                       ):

        if not uuid:
            uuid = self.next_uuid()

        network = Network.new(name=name,
                              namespace=namespace,
                              netblock=netblock,
                              provide_dhcp=provide_dhcp,
                              provide_nat=provide_nat,
                              uuid=uuid,
                              vxid=vxid,
                              )

        self.persist_metadata(Network, network.uuid, metadata)

        state_path = defaultdict(set)
        for initial, allowed in Network.state_targets.items():
            if allowed:
                for a in allowed:
                    state_path[a].add(initial)

        self._find_start(network, state_path, Network.STATE_INITIAL, set_state)
        network.state = set_state

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
                'macaddr': mac_address,
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

        self._find_start(net_iface, state_path, NetworkInterface.STATE_INITIAL,
                         set_state)
        net_iface.state = set_state

        return net_iface
