import os
import time
import uuid
from collections import defaultdict
from typing import Any, Optional, Union

import semver
from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import GiB
from shakenfist.eventlog import add_event
from shakenfist.exceptions import NoSuchDaemon
from shakenfist.exceptions import NoSuchDaemonState
from shakenfist.schema.node_attributes import NodeAttributesData
from shakenfist.schema.node_data import NodeData
from shakenfist.schema.object_reference import references_to_grouped_dict
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.util import callstack as util_callstack
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


class Node(dbo):
    object_type = ObjectType.NODE
    initial_version = 10
    current_version = 11

    # docs/developer_guide/state_machine.md has a description of these states.
    STATE_MISSING = 'missing'
    STATE_STOPPING = 'stopping'
    STATE_STOPPED = 'stopped'
    STATE_DEGRADED = 'degraded'

    # Note that this list of active states is duplicated in baseobject as well to avoid
    # a circular import, and if changed must be updated there as well.
    ACTIVE_STATES = {dbo.STATE_INITIAL, dbo.STATE_CREATED, STATE_DEGRADED}
    INACTIVE_STATES = {dbo.STATE_DELETED, dbo.STATE_ERROR, STATE_MISSING}

    # Remember that this list must align with what sf-ctl is called with in deploy.yml.
    VALID_DAEMONS = [
        'database', 'eventlog', 'net', 'resources', 'sidechannel', 'queues',
        'api', 'checksums', 'cleaner', 'cluster', 'transfers', 'privexec',
        'nodelock', 'sentinel-first', 'sentinel-last'
    ]

    DAEMON_STATE_RUNNING = 'daemon-running'
    DAEMON_STATE_STOPPING = 'daemon-stopping'
    DAEMON_STATE_STOPPED = 'daemon-stopped'
    VALID_DAEMON_STATES = [
        DAEMON_STATE_RUNNING, DAEMON_STATE_STOPPING, DAEMON_STATE_STOPPED
    ]

    state_targets = {
        None: (dbo.STATE_INITIAL),
        dbo.STATE_INITIAL: (
            dbo.STATE_CREATED, dbo.STATE_ERROR, STATE_MISSING, STATE_DEGRADED),
        dbo.STATE_CREATED: (
            dbo.STATE_DELETED, dbo.STATE_ERROR, STATE_MISSING, STATE_STOPPING,
            STATE_DEGRADED),
        STATE_STOPPING: (
            STATE_STOPPED, dbo.STATE_DELETED, dbo.STATE_ERROR, STATE_DEGRADED,
            dbo.STATE_CREATED),
        STATE_STOPPED: (
            dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR, STATE_DEGRADED),

        # Some (but not all) components are not running correctly on the node
        STATE_DEGRADED: (
            dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR, STATE_MISSING,
            STATE_STOPPING),

        # A node can return from the dead...
        dbo.STATE_ERROR: (dbo.STATE_CREATED, dbo.STATE_DELETED, STATE_DEGRADED),
        STATE_MISSING: (
            dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR, STATE_DEGRADED),

        # And even from being deleted. This is because you need to delete the node to
        # get the instances on the node to be marked as gone, but then you might repair
        # the node and return it to service.
        dbo.STATE_DELETED: (dbo.STATE_CREATED),
    }

    def __init__(self, data: NodeData) -> None:
        # Apply lazy upgrades to the immutable Pydantic model
        data = self.upgrade_pydantic_data(data, NodeData)
        super().__init__(data.uuid, data.version)

        self.__fqdn: str = data.fqdn
        self.__ip: str = data.ip

        # Override log field to use fqdn for readability
        self.log = LOG.with_fields({self.object_type: self.__fqdn})

        # Lazy-load attributes from MariaDB
        self.__attributes: Optional[NodeAttributesData] = None
        self.__attributes_loaded: bool = False

    def _load_attributes(self) -> Optional[NodeAttributesData]:
        """Load attributes from MariaDB."""
        if not self.__attributes_loaded:
            self.__attributes = mariadb.get_node_attributes(self.uuid)
            self.__attributes_loaded = True
        return self.__attributes

    def _ensure_attributes(self) -> NodeAttributesData:
        """Ensure attributes record exists, creating defaults if needed."""
        attrs = self._load_attributes()
        if attrs is None:
            attrs = NodeAttributesData(uuid=self.uuid)
            if not mariadb.create_node_attributes(attrs):
                # Another thread/process created the record first;
                # reload the actual data from MariaDB.
                attrs = mariadb.get_node_attributes(self.uuid)
            self.__attributes = attrs
        return attrs

    def _save_attributes(self) -> None:
        """Persist current attributes to MariaDB."""
        if self.__attributes is not None:
            mariadb.update_node_attributes(self.__attributes)

    def _invalidate_attributes(self) -> None:
        """Force reload of attributes on next access."""
        self.__attributes_loaded = False
        self.__attributes = None

    @classmethod
    def _upgrade_step_10_to_11(cls, static_values):
        # Migration to MariaDB nodes/node_attributes tables is handled by
        # sf-ctl migrate-data-to-mariadb.
        ...

    @classmethod
    def _persist_pydantic_upgrade(  # type: ignore[override]
            cls, data: NodeData) -> None:
        """Persist an upgraded NodeData to MariaDB."""
        mariadb.update_node(data)

    @classmethod
    def _db_create(cls, object_uuid: str, metadata: dict[str, Any]) -> None:
        """Create a node record in MariaDB."""
        node_uuid = metadata.get('uuid', object_uuid)
        if isinstance(node_uuid, str):
            node_uuid = uuid.UUID(node_uuid)

        mariadb.create_node(
            node_uuid, metadata['fqdn'], metadata['ip'], metadata['version'])
        add_event(
            EVENT_TYPE_AUDIT, cls.object_type, str(node_uuid), 'db record created',
            extra={
                'fqdn': metadata['fqdn'],
                'ip': metadata['ip'],
                'version': metadata['version']
            })

    @classmethod
    def _db_get(cls, identifier: Union[str, uuid.UUID]) -> Optional[NodeData]:
        """Get node static values from MariaDB.

        Supports lookup by UUID or FQDN for backwards compatibility.
        """
        data = None

        if isinstance(identifier, uuid.UUID):
            data = mariadb.get_node(identifier)
        else:
            # Try parsing as UUID string first
            try:
                parsed = uuid.UUID(str(identifier))
                data = mariadb.get_node(parsed)
            except ValueError:
                pass

            # Fall back to FQDN lookup
            if data is None:
                data = mariadb.get_node_by_fqdn(str(identifier))

        if data is None:
            return None

        if data.version != cls.current_version:
            if not cls.upgrade_supported:
                from shakenfist import exceptions
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - {cls.object_type}: {data}')
        return data

    @classmethod
    def from_db(cls, identifier: Union[str, uuid.UUID],
                suppress_failure_audit: bool = False) -> Optional['Node']:
        """Load a Node from the database.

        Supports lookup by UUID or FQDN.
        """
        if not identifier:
            return None

        data = cls._db_get(identifier)
        if not data:
            if not suppress_failure_audit:
                add_event(
                    EVENT_TYPE_AUDIT, cls.object_type, str(identifier),
                    'attempt to lookup non-existent object',
                    extra={'caller': util_callstack.get_caller(offset=-3)},
                    log_as_error=True)
            return None

        return cls(data)

    @classmethod
    def new(cls, name, ip):
        # Try to find existing node by FQDN
        n = Node.from_db(name, suppress_failure_audit=True)
        if n:
            return n

        # Generate a real UUID4 for the new node
        node_uuid = uuid.uuid4()
        Node._db_create(str(node_uuid), {
            'uuid': node_uuid,
            'fqdn': name,
            'ip': ip,
            'version': cls.current_version
        })
        n = Node.from_db(str(node_uuid))
        n.state = cls.STATE_INITIAL
        n.add_event(EVENT_TYPE_AUDIT, 'node created')
        return n

    @classmethod
    def _node_uuid_path(cls):
        """Return the path to the local node UUID file."""
        return os.path.join(config.STORAGE_PATH, 'node_uuid')

    @classmethod
    def _load_persisted_uuid(cls):
        """Load node UUID from config or local file.

        Returns the UUID string if found and valid, else None.
        """
        # Config / environment variable takes precedence
        if config.NODE_UUID:
            try:
                uuid.UUID(config.NODE_UUID)
                return config.NODE_UUID
            except ValueError:
                LOG.warning('Invalid NODE_UUID in config, ignoring: %s',
                            config.NODE_UUID)

        # Fall back to local file
        path = cls._node_uuid_path()
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    stored = f.read().strip()
                uuid.UUID(stored)
                return stored
            except (ValueError, OSError) as e:
                LOG.warning('Failed to read node UUID from %s: %s',
                            path, e)

        return None

    @classmethod
    def _persist_uuid(cls, node_uuid):
        """Write node UUID to local file for fast startup."""
        path = cls._node_uuid_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(str(node_uuid))
            LOG.info('Persisted node UUID to %s', path)
        except OSError as e:
            LOG.warning('Failed to persist node UUID to %s: %s',
                        path, e)

    @classmethod
    def this_node(cls, suppress_failure_audit=False):
        """Look up the current node, using persisted UUID if available.

        This is more efficient than from_db(config.NODE_NAME) as it
        avoids the FQDN-to-UUID indirection when the UUID is persisted
        locally.
        """
        persisted = cls._load_persisted_uuid()
        if persisted:
            n = cls.from_db(
                persisted,
                suppress_failure_audit=suppress_failure_audit)
            if n and n.fqdn == config.NODE_NAME:
                return n

        return cls.from_db(
            config.NODE_NAME,
            suppress_failure_audit=suppress_failure_audit)

    @classmethod
    def observe_this_node(cls):
        """Upsert this node and update its attributes."""
        n = cls.this_node(suppress_failure_audit=True)
        if not n:
            n = cls.new(config.NODE_NAME, config.NODE_MESH_IP)
            cls._persist_uuid(n.uuid)

        attrs = n._ensure_attributes()
        attrs.last_seen = time.time()
        attrs.installed_version = util_general.get_version()
        attrs.is_etcd_master = False
        attrs.is_hypervisor = config.NODE_IS_HYPERVISOR
        attrs.is_network_node = config.NODE_IS_NETWORK_NODE
        attrs.is_eventlog_node = config.NODE_IS_EVENTLOG_NODE
        n._save_attributes()

    def external_view(self):
        """Build a dict of node state for the API."""
        retval = self._external_view()
        retval.update({
            'fqdn': self.fqdn,
            'name': self.fqdn,
            'ip': self.ip,
            'lastseen': self.last_seen,
            'release': self.installed_version
        })

        # Add role flags from attributes
        attrs = self._load_attributes()
        if attrs:
            retval['is_etcd_master'] = attrs.is_etcd_master
            retval['is_hypervisor'] = attrs.is_hypervisor
            retval['is_network_node'] = attrs.is_network_node
            retval['is_eventlog_node'] = attrs.is_eventlog_node

        # Add daemon states
        for daemon in self.VALID_DAEMONS:
            retval[f'daemon-{daemon}-state'] = self.get_daemon_state(daemon).value

        # Object references use FQDN as the node identifier in the
        # object_references table.
        refs_to = mariadb.get_references_to(ObjectType.NODE, self.fqdn)
        refs_from = mariadb.get_references_from(ObjectType.NODE, self.fqdn)
        retval['references_to'] = references_to_grouped_dict(refs_to)
        retval['references_from'] = references_to_grouped_dict(refs_from)

        return retval

    def get_registered_daemons(self):
        attrs = self._load_attributes()
        if attrs is None:
            return []
        return list(attrs.daemons)

    def register_daemon(self, daemon):
        if daemon not in self.VALID_DAEMONS:
            raise NoSuchDaemon(
                f'Cannot register daemon "{daemon}" on node {self.fqdn}, '
                f'as that daemon is unknown.')
        with self.get_lock_attr('daemons', 'Register daemon'):
            self._invalidate_attributes()
            attrs = self._ensure_attributes()
            if daemon not in attrs.daemons:
                attrs.daemons.append(daemon)
                self._save_attributes()
        self.set_daemon_state(daemon, self.DAEMON_STATE_STOPPED)
        self.add_event(EVENT_TYPE_AUDIT, f'{daemon} daemon registered')

    def deregister_daemon(self, daemon):
        if daemon not in self.VALID_DAEMONS:
            raise NoSuchDaemon(
                f'Cannot deregister daemon "{daemon}" on node {self.fqdn}, '
                f'as that daemon is unknown.')
        with self.get_lock_attr('daemons', 'Deregister daemon'):
            self._invalidate_attributes()
            attrs = self._ensure_attributes()
            if daemon in attrs.daemons:
                attrs.daemons.remove(daemon)
            if daemon in attrs.daemon_states:
                del attrs.daemon_states[daemon]
            self._save_attributes()
        self.add_event(EVENT_TYPE_AUDIT, f'{daemon} daemon deregistered')

    def set_daemon_state(self, daemon, state, message=None):
        if daemon not in self.VALID_DAEMONS:
            raise NoSuchDaemon(
                f'Cannot set daemon state for "{daemon}" on node {self.fqdn}, '
                f'as that daemon is unknown.')
        if state not in self.VALID_DAEMON_STATES:
            raise NoSuchDaemonState(f'The daemon state {state} does not exist')

        changed = False
        with self.get_lock_attr('daemon_states', f'Set {daemon} state'):
            self._invalidate_attributes()
            attrs = self._ensure_attributes()
            current = attrs.daemon_states.get(daemon, {})
            if current.get('value') != state:
                attrs.daemon_states[daemon] = {
                    'value': state,
                    'update_time': time.time(),
                    'message': message
                }
                self._save_attributes()
                changed = True

        if not changed:
            return

        # Determine if the node should transition state based on this update
        degraded = self.get_degraded_daemons()
        degraded_or_stopping = [
            self.STATE_DEGRADED, self.STATE_STOPPING, self.STATE_STOPPED]
        node_state = self.state.value

        if node_state not in degraded_or_stopping and degraded:
            self.add_event(
                EVENT_TYPE_AUDIT,
                'node is not stopping or stopped, but a daemon is not running '
                'so entering degraded state',
                extra={'degraded': degraded})
            self.state = self.STATE_DEGRADED
        elif node_state == self.STATE_DEGRADED and not degraded:
            self.add_event(EVENT_TYPE_AUDIT, 'node is no longer degraded')
            self.state = self.STATE_CREATED

    def get_daemon_state(self, daemon):
        if daemon not in self.VALID_DAEMONS:
            raise NoSuchDaemon(
                f'Cannot get daemon state for "{daemon}" on node {self.fqdn}, '
                f'as that daemon is unknown.')
        attrs = self._load_attributes()
        if attrs is None:
            return State(value=None, update_time=0)
        ds = attrs.daemon_states.get(daemon)
        if not ds:
            return State(value=None, update_time=0)
        return State(**ds)

    def get_degraded_daemons(self):
        degraded = []
        for daemon in self.get_registered_daemons():
            daemon_state = self.get_daemon_state(daemon).value
            if not daemon_state:
                degraded.append(daemon)
            if daemon_state in [self.DAEMON_STATE_STOPPING, self.DAEMON_STATE_STOPPED]:
                degraded.append(daemon)
        return degraded

    # Static values (immutable, from NodeData)
    @property
    def ip(self):
        return self.__ip

    @property
    def fqdn(self):
        return self.__fqdn

    # Attribute-backed properties (from NodeAttributesData in MariaDB)
    @property
    def last_seen(self):
        attrs = self._load_attributes()
        if attrs is None:
            return 0
        return attrs.last_seen

    @property
    def installed_version(self):
        attrs = self._load_attributes()
        if attrs is None:
            return None
        return attrs.installed_version

    @property
    def blobs(self):
        """Return list of blob UUIDs present on this node.

        Queries the object_references table for BLOB_LOCATION relationships
        where this node is the source.
        """
        refs = mariadb.get_references_from(
            ObjectType.NODE, self.fqdn, RelationshipType.BLOB_LOCATION)
        return [str(ref.target_uuid) for ref in refs]

    @property
    def instances(self):
        attrs = self._load_attributes()
        if attrs is None:
            return []
        return list(attrs.instances)

    @instances.setter
    def instances(self, value):
        attrs = self._ensure_attributes()
        attrs.instances = list(value)
        self._save_attributes()

    def add_instance(self, instance_uuid):
        with self.get_lock_attr('instances', 'Add instance'):
            self._invalidate_attributes()
            attrs = self._ensure_attributes()
            instance_str = str(instance_uuid)
            if instance_str not in attrs.instances:
                attrs.instances.append(instance_str)
                self._save_attributes()

    def remove_instance(self, instance_uuid):
        with self.get_lock_attr('instances', 'Remove instance'):
            self._invalidate_attributes()
            attrs = self._ensure_attributes()
            instance_str = str(instance_uuid)
            if instance_str in attrs.instances:
                attrs.instances.remove(instance_str)
                self._save_attributes()

    @property
    def dependency_versions(self):
        attrs = self._load_attributes()
        if attrs is None:
            return {}
        return dict(attrs.dependency_versions)

    @dependency_versions.setter
    def dependency_versions(self, value):
        attrs = self._ensure_attributes()
        if value != attrs.dependency_versions:
            attrs.dependency_versions = dict(value)
            self._save_attributes()

    def _version_tuple_to_semver(self, t):
        while len(t) < 3:
            t.append(0)
        return semver.Version(major=t[0], minor=t[1], patch=t[2])

    @property
    def qemu_version(self):
        attrs = self._load_attributes()
        if attrs is None or attrs.qemu_version is None:
            return self._version_tuple_to_semver([0, 0, 0])
        return self._version_tuple_to_semver(list(attrs.qemu_version))

    @qemu_version.setter
    def qemu_version(self, value):
        attrs = self._ensure_attributes()
        new_val = list(value)
        if new_val != (attrs.qemu_version or []):
            attrs.qemu_version = new_val
            self._save_attributes()

    @property
    def libvirt_version(self):
        attrs = self._load_attributes()
        if attrs is None or attrs.libvirt_version is None:
            return self._version_tuple_to_semver([0, 0, 0])
        return self._version_tuple_to_semver(list(attrs.libvirt_version))

    @libvirt_version.setter
    def libvirt_version(self, value):
        attrs = self._ensure_attributes()
        new_val = list(value)
        if new_val != (attrs.libvirt_version or []):
            attrs.libvirt_version = new_val
            self._save_attributes()

    @property
    def python_version(self):
        attrs = self._load_attributes()
        if attrs is None or attrs.python_version is None:
            return self._version_tuple_to_semver([0, 0, 0])
        return self._version_tuple_to_semver(list(attrs.python_version))

    @python_version.setter
    def python_version(self, value):
        attrs = self._ensure_attributes()
        new_val = list(value)
        if new_val != (attrs.python_version or []):
            attrs.python_version = new_val
            self._save_attributes()

    @property
    def python_implementation(self):
        attrs = self._load_attributes()
        if attrs is None:
            return None
        return attrs.python_implementation

    @python_implementation.setter
    def python_implementation(self, value):
        attrs = self._ensure_attributes()
        if value != attrs.python_implementation:
            attrs.python_implementation = value
            self._save_attributes()

    @property
    def process_metrics(self):
        attrs = self._load_attributes()
        if attrs is None:
            return {}
        return dict(attrs.process_metrics)

    @process_metrics.setter
    def process_metrics(self, value):
        attrs = self._ensure_attributes()
        if value != attrs.process_metrics:
            attrs.process_metrics = dict(value)
            self._save_attributes()

    def delete(self):
        # NOTE(mikal): the remainder of the cleanup of deleted nodes happens in
        # the cluster maintenance daemon because otherwise we end up in a tangled
        # mess of circular python imports here.
        self.state = self.STATE_DELETED

    def hard_delete(self):
        # We do not hard delete nodes, they're special.
        pass


class Nodes(dbo_iter):
    base_object = Node

    def __iter__(self):
        all_uuids = mariadb.get_all_node_uuids()

        # Apply state prefilter if specified
        if self.prefilter:
            if self.prefilter == 'active':
                target_states = self.base_object.ACTIVE_STATES
            elif self.prefilter == 'deleted':
                target_states = [dbo.STATE_DELETED]
            elif self.prefilter == 'healthy':
                target_states = self.base_object.HEALTHY_STATES
            elif self.prefilter == 'inactive':
                target_states = self.base_object.INACTIVE_STATES
            else:
                from shakenfist import exceptions
                raise exceptions.InvalidObjectPrefilter(self.prefilter)

            matching = mariadb.get_objects_by_state(
                ObjectType.NODE, list(target_states))
            if matching is not None:
                matching = set(matching)
                all_uuids = [u for u in all_uuids if u in matching]

        for node_uuid in all_uuids:
            n = Node.from_db(node_uuid)
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
        metrics_data = mariadb.get_node_metrics(str(n.uuid))
        if metrics_data:
            metrics = metrics_data.get('metrics', {})
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
