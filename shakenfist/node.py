import time
from collections import defaultdict

import semver
from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import GiB
from shakenfist.exceptions import NoSuchDaemon
from shakenfist.exceptions import NoSuchDaemonState
from shakenfist.schema.object_reference import references_to_grouped_dict
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


class Node(dbo):
    object_type = ObjectType.NODE
    initial_version = 2
    current_version = 10

    # docs/developer_guide/state_machine.md has a description of these states.
    STATE_MISSING = 'missing'
    STATE_STOPPING = 'stopping'
    STATE_STOPPED = 'stopped'
    STATE_DEGRADED = 'degraded'

    # Note that this list of active states is duplicated in baseobject as well to
    # avoid a circular import, and if changed must be updated there as well.
    ACTIVE_STATES = {dbo.STATE_INITIAL, dbo.STATE_CREATED, STATE_DEGRADED}
    INACTIVE_STATES = {dbo.STATE_DELETED, dbo.STATE_ERROR, STATE_MISSING}

    # Remember that this list must align with what sf-ctl is called with in
    # deploy.yml.
    VALID_DAEMONS = ['database', 'eventlog', 'net', 'resources', 'sidechannel',
                     'queues', 'api', 'checksums', 'cleaner', 'cluster',
                     'transfers', 'privexec', 'nodelock', 'sentinel-first',
                     'sentinel-last']

    DAEMON_STATE_RUNNING = 'daemon-running'
    DAEMON_STATE_STOPPING = 'daemon-stopping'
    DAEMON_STATE_STOPPED = 'daemon-stopped'
    VALID_DAEMON_STATES = [DAEMON_STATE_RUNNING, DAEMON_STATE_STOPPING,
                           DAEMON_STATE_STOPPED]

    state_targets = {
        None: (dbo.STATE_INITIAL),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_ERROR, STATE_MISSING,
                            STATE_DEGRADED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_ERROR, STATE_MISSING,
                            STATE_STOPPING, STATE_DEGRADED),
        STATE_STOPPING: (STATE_STOPPED, dbo.STATE_DELETED, dbo.STATE_ERROR,
                         STATE_DEGRADED, dbo.STATE_CREATED),
        STATE_STOPPED: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR,
                        STATE_DEGRADED),

        # Some (but not all) components are not running correctly on the node
        STATE_DEGRADED: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR,
                         STATE_MISSING, STATE_STOPPING),

        # A node can return from the dead...
        dbo.STATE_ERROR: (dbo.STATE_CREATED, dbo.STATE_DELETED, STATE_DEGRADED),
        STATE_MISSING: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR,
                        STATE_DEGRADED),

        # And even from being deleted. This is because you need to delete the
        # node to get the instances on the node to be marked as gone, but then
        # you might repair the node and return it to service.
        dbo.STATE_DELETED: (dbo.STATE_CREATED),
    }

    def __init__(self, static_values):
        self.upgrade(static_values)

        # Node uses the FQDN as its identifier for historical reasons instead
        # of a proper UUID. We store it directly to avoid UUID conversion.
        # The uuid property is overridden to return a string.
        self.__node_fqdn = static_values['fqdn']
        self._DatabaseBackedObject__uuid = self.__node_fqdn  # type: ignore
        self._DatabaseBackedObject__version = static_values.get('version')
        self._DatabaseBackedObject__in_memory_only = False
        self.log = LOG.with_fields({self.object_type: self.__node_fqdn})

        self.__ip = static_values['ip']
        self.__fqdn = static_values['fqdn']

    @property
    def uuid(self) -> str:
        """Return the Node's identifier (FQDN) as a string.

        This overrides the base class to return a string instead of uuid.UUID
        because nodes use their FQDN as their identifier for historical reasons.
        """
        return self.__node_fqdn

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
    def _upgrade_step_7_to_8(cls, static_values):
        ...

    @classmethod
    def _upgrade_step_8_to_9(cls, static_values):
        # State migration to MariaDB is now handled by sf-ctl migrate-state-to-mariadb
        ...

    @classmethod
    def _upgrade_step_9_to_10(cls, static_values):
        # The node.blobs cache has been removed. Blob locations are now queried
        # directly from MariaDB's object_references table (BLOB_LOCATION).
        etcd.delete('attribute/node', static_values['fqdn'], 'blobs')

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
        n.state = cls.STATE_INITIAL
        n.add_event(EVENT_TYPE_AUDIT, 'node created')
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
            'name': self.fqdn,
            'ip': self.ip,
            'lastseen': self.last_seen,
            'release': self.installed_version
        })
        retval.update(self._db_get_attribute('roles', {}))

        # And the states of the various daemons
        for daemon in self.VALID_DAEMONS:
            retval[f'daemon-{daemon}-state'] = \
                self.get_daemon_state(daemon).value

        # Add object references (what references this node and what this node
        # references). Note: Node uuid is actually the FQDN.
        refs_to = mariadb.get_references_to(ObjectType.NODE, self.uuid)
        refs_from = mariadb.get_references_from(ObjectType.NODE, self.uuid)
        retval['references_to'] = references_to_grouped_dict(refs_to)
        retval['references_from'] = references_to_grouped_dict(refs_from)

        return retval

    def get_registered_daemons(self):
        return self._db_get_attribute('daemons').get('daemons', [])

    def register_daemon(self, daemon):
        if daemon not in self.VALID_DAEMONS:
            raise NoSuchDaemon(f'Cannot register daemon "{daemon}" on node '
                               f'{self.uuid}, as that daemon is unknown.')
        self._add_item_in_attribute_list('daemons', daemon)
        self.set_daemon_state(daemon, self.DAEMON_STATE_STOPPED)
        self.add_event(EVENT_TYPE_AUDIT, f'{daemon} daemon registered')

    def deregister_daemon(self, daemon):
        if daemon not in self.VALID_DAEMONS:
            raise NoSuchDaemon(f'Cannot deregister daemon "{daemon}" on node '
                               f'{self.uuid}, as that daemon is unknown.')
        self._remove_item_in_attribute_list('daemons', daemon)
        self._db_delete_attribute(f'daemon:{daemon}')
        self.add_event(EVENT_TYPE_AUDIT, f'{daemon} daemon deregistered')

    def set_daemon_state(self, daemon, state, message=None):
        if daemon not in self.VALID_DAEMONS:
            raise NoSuchDaemon(f'Cannot set daemon state for "{daemon}" on node '
                               f'{self.uuid}, as that daemon is unknown.')
        if state not in self.VALID_DAEMON_STATES:
            raise NoSuchDaemonState(f'The daemon state {state} does not exist')
        self._state_update(state, state_attribute_name=f'daemon:{daemon}',
                           message=message)

        # Determine if the node should transition state based on this update
        degraded = self.get_degraded_daemons()
        degraded_or_stopping = [self.STATE_DEGRADED, self.STATE_STOPPING,
                                self.STATE_STOPPED]
        node_state = self.state.value

        if node_state not in degraded_or_stopping and degraded:
            self.add_event(
                EVENT_TYPE_AUDIT,
                'node is not stopping or stopped, but a daemon is not running '
                'so entering degraded state', extra={'degraded': degraded})
            self.state = self.STATE_DEGRADED
        elif node_state == self.STATE_DEGRADED and not degraded:
            self.add_event(EVENT_TYPE_AUDIT, 'node is no longer degraded')
            self.state = self.STATE_CREATED

    def get_daemon_state(self, daemon):
        if daemon not in self.VALID_DAEMONS:
            raise NoSuchDaemon(f'Cannot get daemon state for "{daemon}" on node '
                               f'{self.uuid}, as that daemon is unknown.')
        return self._state_read(state_attribute_name=f'daemon:{daemon}')

    def get_degraded_daemons(self):
        degraded = []
        for daemon in self.get_registered_daemons():
            daemon_state = self.get_daemon_state(daemon).value
            if not daemon_state:
                degraded.append(daemon)
            if daemon_state in [self.DAEMON_STATE_STOPPING,
                                self.DAEMON_STATE_STOPPED]:
                degraded.append(daemon)
        return degraded

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
        """Return list of blob UUIDs present on this node.

        This queries the object_references table for BLOB_LOCATION relationships
        where this node is the source (meaning the node has the blob).
        """
        refs = mariadb.get_references_from(
            ObjectType.NODE, self.uuid, RelationshipType.BLOB_LOCATION)
        return [str(ref.target_uuid) for ref in refs]

    @property
    def instances(self):
        return self._db_get_attribute('instances').get('instances', [])

    @instances.setter
    def instances(self, value):
        self._db_set_attribute('instances', {'instances': value})

    def add_instance(self, instance_uuid):
        self._add_item_in_attribute_list('instances', str(instance_uuid))

    def remove_instance(self, instance_uuid):
        self._remove_item_in_attribute_list('instances', str(instance_uuid))

    @property
    def dependency_versions(self):
        return self._db_get_attribute('dependency_versions')

    @dependency_versions.setter
    def dependency_versions(self, value):
        if value != self.dependency_versions:
            self._db_set_attribute('dependency_versions', value)

    def _version_tuple_to_semver(self, t):
        while len(t) < 3:
            t.append(0)
        return semver.Version(major=t[0], minor=t[1], patch=t[2])

    @property
    def qemu_version(self):
        v = self._db_get_attribute('qemu_version')
        return self._version_tuple_to_semver(v)

    @qemu_version.setter
    def qemu_version(self, value):
        if list(value) != self._db_get_attribute('qemu_version'):
            self._db_set_attribute('qemu_version', value)

    @property
    def libvirt_version(self):
        v = self._db_get_attribute('libvirt_version')
        return self._version_tuple_to_semver(v)

    @libvirt_version.setter
    def libvirt_version(self, value):
        if list(value) != self._db_get_attribute('libvirt_version'):
            self._db_set_attribute('libvirt_version', value)

    @property
    def python_version(self):
        v = self._db_get_attribute('python_version')
        return self._version_tuple_to_semver(v)

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
        for _, static_values in self.get_iterator():
            uniq = static_values.get('uuid')
            if not uniq:
                uniq = static_values.get('fqdn')
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
