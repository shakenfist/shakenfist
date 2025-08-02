import os

import psutil
from shakenfist_utilities import logs  # noreorder

from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.etcd_schema.operations import node_inst_op as schema
from shakenfist.instance import Instance
from shakenfist.instance import Instances
from shakenfist.instance import this_node_filter
from shakenfist.network.network import Network
from shakenfist.network.interface import interfaces_for_instance
from shakenfist.network.interface import NetworkInterface
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt


LOG, HANDLER = logs.setup(__name__)


class NodeInstOpException(BaseOperationException):
    def __init__(self, op, message):
        super().__init__(message)
        self.op_type = op.object_type
        self.op_uuid = op.uuid
        self.node_uuid = op.node_uuid
        self.instance_uuid = op.instance_uuid


class NoSuchTask(NodeInstOpException):
    def __init__(self, op, task):
        super().__init__(op, f'no such task {task}')


class NoSuchInstance(NodeInstOpException):
    def __init__(self, op):
        super().__init__(op, 'instance missing')


class NodeInstOp(BaseClusterOperation):
    object_type = schema.object_type.name.lower()
    initial_version = schema.initial_version
    current_version = schema.current_version

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values, schema)

        self.__node_uuid = static_values['node_uuid']
        self.__instance_uuid = static_values['instance_uuid']

        self.log = LOG.with_fields({
            'operation_type': self.object_type,
            'operation_uuid': self.uuid,
            'node_uuid': self.node_uuid,
            'instance_uuid': self.instance_uuid,
            'tasks': self.tasks
        })

    # Static values
    @property
    def node_uuid(self):
        return self.__node_uuid

    @property
    def instance_uuid(self):
        return self.__instance_uuid

    # API
    def external_view(self):
        retval = super().external_view()
        retval.update({
            'node_uuid': self.node_uuid,
            'instance_uuid': self.instance_uuid
        })
        return retval

    # Tasks
    def dispatch_task(self, task):
        if task not in schema.model_tasks:
            self.log.warning(f'Task {task} not in {schema.model_tasks}')
            raise NoSuchTask(self, task)

        inst = Instance.from_db(self.instance_uuid)
        if not inst:
            self.log.warning(f'Instance {self.instance_uuid} missing')
            raise NoSuchInstance(self)

        try:
            self.__getattribute__(f'_{task.name}')(inst)
        except Exception as e:
            util_general.ignore_exception('node_inst_op', e)
            self.state = NodeInstOp.STATE_ERROR
            if inst:
                inst.state = Instance.STATE_ERROR

    def _collect_billing_statistics(self, inst):
        with util_libvirt.LibvirtConnection() as lc:
            try:
                # The basic structure
                statistics = {
                    'cpu usage': {},
                    'disk usage': {},
                    'network usage': {}
                }

                # Base libvirt statistics, if the domaine exists
                domain = lc.get_domain_from_sf_uuid(inst.uuid)
                if domain:
                    statistics.update(util_libvirt.extract_statistics(domain))

                    # Power information
                    statistics['libvirt_raw_power_state'] = \
                        lc.extract_power_state_pretty(domain)
                    statistics['power_state'] = \
                        lc.extract_power_state(domain)

                # Add in actual size on disk
                bd = inst.block_devices
                if bd:
                    for disk in bd.get('devices', [{}]):
                        disk_path = disk.get('path')
                        disk_device = disk.get('device')
                        if disk_path and disk_device and os.path.exists(disk_path):
                            # Because nvme disks don't exist as full libvirt
                            # disks, they are missing from the statistics
                            # results.
                            if disk_device not in statistics['disk usage']:
                                statistics['disk usage'][disk_device] = {
                                }

                            statistics['disk usage'][disk_device][
                                'actual bytes on disk'] = os.stat(disk_path).st_size

                # Console log size
                console_path = os.path.join(inst.instance_path, 'console.log')
                if os.path.exists(console_path):
                    st = os.stat(console_path)
                    statistics['console_log_size'] = st.st_size
                else:
                    statistics['console_log_size'] = 0

                # Add in OOM details
                try:
                    pid = inst.kvm_pid
                    if pid:
                        with open('/proc/%s/oom_score' % pid) as f:
                            statistics['oom_score'] = f.read()
                        with open('/proc/%s/oom_score_adj' % pid) as f:
                            statistics['oom_score_adj'] = f.read()

                except FileNotFoundError:
                    ...

                inst.add_event(
                    EVENT_TYPE_USAGE, 'usage', extra=statistics,
                    suppress_event_logging=True)

            except lc.libvirt.libvirtError as e:
                self.log.warning('Ignoring libvirt error: %s' % e)

    def _health_check_kvm_process(self, inst):
        pid = inst.kvm_pid
        if pid:
            try:
                psutil.Process(pid)
            except (psutil.NoSuchProcess, FileNotFoundError):
                inst.kvm_pid = None

                if inst.power_state == 'on':
                    inst.enqueue_delete_due_error('kvm process missing')

    def _instance_delete(self, inst):
        with inst.get_lock(op='Instance delete', global_scope=False):
            # There are two delete state flows:
            #   - error transition states (preflight-error etc) to error
            #   - created to deleted

            # If the instance is deleted already, we're done here.
            if inst.state.value == Instance.STATE_DELETED:
                return

            # We don't need delete_wait for the error states as they're already
            # in a transition state.
            if inst.state.value not in Instance.ERROR_STATES:
                inst.state = Instance.STATE_DELETE_WAIT

            # Create list of networks used by instance. We cannot use the
            # interfaces cached in the instance here, because the instance
            # may have failed to get to the point where it populates that
            # field (an image fetch failure for example).
            instance_networks = []
            interfaces = []
            for ni in interfaces_for_instance(inst):
                if ni:
                    interfaces.append(ni)
                    if ni.network_uuid not in instance_networks:
                        instance_networks.append(ni.network_uuid)

            # Stop the instance
            inst.power_off()

            # Delete the instance's interfaces
            for ni in interfaces:
                ni.delete()

            # Create list of networks used by all other instances
            host_networks = []
            for i in Instances([this_node_filter], prefilter='active'):
                if not i.uuid == inst.uuid:
                    for iface_uuid in inst.interfaces:
                        ni = NetworkInterface.from_db(iface_uuid)
                        if ni and ni.network_uuid not in host_networks:
                            host_networks.append(ni.network_uuid)

            inst.delete()

            # Check each network used by the deleted instance
            for network_uuid in instance_networks:
                n = Network.from_db(network_uuid)
                if not n:
                    continue

                if n.state.value == Network.STATE_DELETE_WAIT:
                    continue

                n.update_dnsmasq()

                if (not config.NODE_IS_NETWORK_NODE and
                        network_uuid not in host_networks):
                    # We are not the network node and the network not used by any
                    # other instance on this hypervisor, therefore clean it up
                    n.delete_on_hypervisor()
