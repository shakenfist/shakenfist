import os

import psutil
from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.etcd_schema.operations import node_inst_op as schema
from shakenfist.instance import Instance
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
        self.instance_uuid = op.instance_uuid
        self.node_uuid = op.node_uuid


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
        super().__init__(static_values)

        self.__node_uuid = static_values['node_uuid']
        self.__instance_uuid = static_values['instance_uuid']

        # Convert tasks names back into enum entries
        self.__tasks = []
        for task_name in static_values['tasks']:
            try:
                self.__tasks.append(schema.model_tasks[task_name])
            except KeyError as e:
                self.state = self.STATE_ERROR
                self.add_event(
                    EVENT_TYPE_AUDIT, 'unknown task {task_name}: {e}')
                raise e

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

    @property
    def tasks(self):
        return self.__tasks

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
            if self.inst:
                self.inst.state = Instance.STATE_ERROR

    def _collect_billing_statistics(self, inst):
        with util_libvirt.LibvirtConnection() as lc:
            try:
                statistics = {}

                # Base libvirt statistics
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
                inst.state = Instance.STATE_ERROR
                inst.add_event(EVENT_TYPE_AUDIT, 'kvm process missing')
