from collections import defaultdict
from shakenfist_utilities import logs
import time
import versions

from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist.config import config
from shakenfist.constants import GiB
from shakenfist import etcd
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


class Node(dbo):
    object_type = 'node'
    initial_version = 2
    current_version = 7

    # docs/developer_guide/state_machine.md has a description of these states.
    STATE_MISSING = 'missing'
    STATE_STOPPING = 'stopping'
    STATE_STOPPED = 'stopped'

    ACTIVE_STATES = {dbo.STATE_CREATED}
    INACTIVE_STATES = {dbo.STATE_DELETED, dbo.STATE_ERROR, STATE_MISSING}

    state_targets = {
        None: (dbo.STATE_CREATED, dbo.STATE_ERROR, STATE_MISSING),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_ERROR, STATE_MISSING,
                            STATE_STOPPING),
        STATE_STOPPING: (STATE_STOPPED, dbo.STATE_DELETED, dbo.STATE_ERROR,
                         dbo.STATE_CREATED),
        STATE_STOPPED: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),

        # A node can return from the dead...
        dbo.STATE_ERROR: (dbo.STATE_CREATED, dbo.STATE_DELETED),
        STATE_MISSING: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),

        # But not from being deleted.
        dbo.STATE_DELETED: None,
    }

    def __init__(self, static_values):
        self.upgrade(static_values)

        # We treat a node name as a UUID here for historical reasons
        super().__init__(static_values['fqdn'], static_values.get('version'))

        self.__ip = static_values['ip']
        self.__fqdn = static_values['fqdn']

    @classmethod
    def _upgrade_step_2_to_3(cls, static_values):
        ...

    @classmethod
    def _upgrade_step_3_to_4(cls, static_values):
        ...

    @classmethod
    def _upgrade_step_4_to_5(cls, static_values):
        ...

    @classmethod
    def _upgrade_step_5_to_6(cls, static_values):
        ...

    @classmethod
    def _upgrade_step_6_to_7(cls, static_values):
        etcd.delete('attribute/node',  static_values['fqdn'], 'instances-active')

    @classmethod
    def new(cls, name, ip):
        n = Node.from_db(name, suppress_failure_audit=True)
        if n:
            return n

        Node._db_create(name, {
            'fqdn': name,
            'ip': ip,
            'version': cls.current_version
        })
        n = Node.from_db(name)
        n.state = cls.STATE_CREATED
        return n

    @classmethod
    def observe_this_node(cls):
        # We use Node.new here because it acts like a "upsert". It will create
        # the node object if it doesn't already exist, and otherwise use the
        # existing one.
        n = cls.new(config.NODE_NAME, config.NODE_MESH_IP)
        n._db_set_attribute('observed',
                            {
                                'at': time.time(),
                                'release': util_general.get_version()
                            })

        roles = {
            'is_etcd_master': config.NODE_IS_ETCD_MASTER,
            'is_hypervisor': config.NODE_IS_HYPERVISOR,
            'is_network_node': config.NODE_IS_NETWORK_NODE,
            'is_eventlog_node': config.NODE_IS_EVENTLOG_NODE
        }
        if n._db_get_attribute('roles') != roles:
            n._db_set_attribute('roles', roles)

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        retval = self._external_view()
        retval.update({
            'fqdn': self.fqdn,
            'ip': self.ip,
            'lastseen': self.last_seen,
            'release': self.installed_version
        })
        retval.update(self._db_get_attribute('roles', {}))
        return retval

    # Static values
    @property
    def ip(self):
        return self.__ip

    @property
    def fqdn(self):
        return self.__fqdn

    # Values routed to attributes, writes are via helper methods.
    @property
    def last_seen(self):
        return self._db_get_attribute('observed').get('at', 0)

    @property
    def installed_version(self):
        return self._db_get_attribute('observed').get('release')

    @property
    def blobs(self):
        return self._db_get_attribute('blobs').get('blobs', [])

    @blobs.setter
    def blobs(self, value):
        self._db_set_attribute('blobs', {'blobs': value})

    def add_blob(self, blob):
        self._add_item_in_attribute_list('blobs', blob)

    def remove_blob(self, blob):
        self._remove_item_in_attribute_list('blobs', blob)

    @property
    def instances(self):
        return self._db_get_attribute('instances').get('instances', [])

    @instances.setter
    def instances(self, value):
        self._db_set_attribute('instances', {'instances': value})

    def add_instance(self, instance_uuid):
        self._add_item_in_attribute_list('instances', instance_uuid)

    def remove_instance(self, instance_uuid):
        self._remove_item_in_attribute_list('instances', instance_uuid)

    @property
    def dependency_versions(self):
        return self._db_get_attribute('dependency_versions')

    @dependency_versions.setter
    def dependency_versions(self, value):
        if value != self.dependency_versions:
            self._db_set_attribute('dependency_versions', value)

    @property
    def qemu_version(self):
        v = self._db_get_attribute('qemu_version')
        return versions.version.Version.from_parts(*v)

    @qemu_version.setter
    def qemu_version(self, value):
        if list(value) != self._db_get_attribute('qemu_version'):
            self._db_set_attribute('qemu_version', value)

    @property
    def libvirt_version(self):
        v = self._db_get_attribute('libvirt_version')
        return versions.version.Version.from_parts(*v)

    @libvirt_version.setter
    def libvirt_version(self, value):
        if list(value) != self._db_get_attribute('libvirt_version'):
            self._db_set_attribute('libvirt_version', value)

    @property
    def python_version(self):
        v = self._db_get_attribute('python_version')
        return versions.version.Version.from_parts(*v)

    @python_version.setter
    def python_version(self, value):
        if list(value) != self._db_get_attribute('python_version'):
            self._db_set_attribute('python_version', value)

    @property
    def python_implementation(self):
        return self._db_get_attribute('python_implementation')

    @python_implementation.setter
    def python_implementation(self, value):
        if value != self._db_get_attribute('python_implementation'):
            self._db_set_attribute('python_implementation', value)

    @property
    def process_metrics(self):
        return self._db_get_attribute('process_metrics')

    @process_metrics.setter
    def process_metrics(self, value):
        if value != self.process_metrics:
            self._db_set_attribute('process_metrics', value)

    def delete(self):
        # NOTE(mikal): the remainder of the cleanup of deleted nodes happens
        # in the cluster maintenance daemon because otherwise we end up in a
        # tangled mess of circular python imports here.
        self.state = self.STATE_DELETED

    def hard_delete(self):
        # We do not hard delete nodes, they're special.
        pass


class Nodes(dbo_iter):
    base_object = Node

    def __iter__(self):
        for _, n in self.get_iterator():
            uniq = n.get('uuid')
            if not uniq:
                uniq = n.get('fqdn')
            if not uniq:
                continue

            n = Node.from_db(uniq)
            if not n:
                continue

            out = self.apply_filters(n)
            if out:
                yield out


def _sort_by_key(d):
    for k in sorted(d, reverse=True):
        yield from d[k]


def nodes_by_free_disk_descending(minimum=0, maximum=-1, intention=None):
    by_disk = defaultdict(list)
    if not intention:
        intention = ''
    else:
        intention = '_%s' % intention

    for n in Nodes([], prefilter='active'):
        metrics = etcd.get('metrics', n.fqdn, None)
        if metrics:
            metrics = metrics.get('metrics', {})
        else:
            metrics = {}

        disk_free_gb = int(
            int(metrics.get('disk_free%s' % intention, '0')) / GiB)

        if disk_free_gb < minimum:
            continue
        if maximum != -1 and disk_free_gb > maximum:
            continue

        by_disk[disk_free_gb].append(n.fqdn)

    return list(_sort_by_key(by_disk))
