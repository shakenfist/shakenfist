import os
from uuid import uuid4

import psutil

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.etcd_schema.operations.baseclusteroperation import PRIORITY
from shakenfist.instance import Instance
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import BaseOperationException
from shakenfist.operations.baseoperation import InvalidPriorityException
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt


class NodeInstanceOperationException(BaseOperationException):
    def __init__(self, task, message):
        super().__init__(message)
        self.task_type = task.object_type
        self.task_uuid = task.uuid
        self.instance_uuid = task.instance_uuid
        self.node_uuid = task.node_uuid


class NoSuchTask(NodeInstanceOperationException):
    def __init__(self, task):
        super().__init__(task, 'no such task')


class NoSuchInstance(NodeInstanceOperationException):
    def __init__(self, task):
        super().__init__(task, 'instance missing')


class NodeInstanceOperation(BaseClusterOperation):
    object_type = 'nodeinstanceoperation'
    initial_version = 1
    current_version = 1

    def __init__(self, static_values):
        self.upgrade(static_values)
        super().__init__(static_values)

        self.__instance_uuid = static_values['instance_uuid']
        self.__tasks = static_values['tasks']

    @classmethod
    def new(cls, node_uuid, instance_uuid, tasks, priority, request_id=None):
        if priority not in PRIORITY:
            raise InvalidPriorityException(priority)

        operation_uuid = str(uuid4())
        NodeInstanceOperation._db_create(operation_uuid, {
            'uuid': operation_uuid,
            'node_uuid': node_uuid,
            'instance_uuid': instance_uuid,
            'priority': priority,
            'request_id': request_id,
            'tasks': tasks,
            'version': cls.current_version
        })
        o = NodeInstanceOperation.from_db(operation_uuid)
        o.state = cls.STATE_INITIAL
        return o

    # Static values
    @property
    def instance_uuid(self):
        return self.__instance_uuid

    @property
    def tasks(self):
        return self.__tasks

    # Tasks
    _all_tasks = [
        'collect_billing_statistics',
        'health_check_kvm_process'
    ]

    def dispatch_task(self, task):
        if task not in self._all_tasks:
            raise NoSuchTask(task)

        inst = Instance.from_db(self.instance_uuid)
        if not inst:
            raise NoSuchInstance(task)

        try:
            self.__getattribute__(f'_{task}')(inst)
        except Exception as e:
            util_general.ignore_exception('nodeinstanceoperation', e)

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
