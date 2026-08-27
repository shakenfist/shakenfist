import json
import time
import uuid
from collections import defaultdict
from functools import partial
from math import inf
import re
from typing import Any
from typing import ClassVar
from typing import Optional
from typing import TypeVar
from typing import Union

from pydantic import BaseModel

from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import get_object_class
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.constants import NODE_ACTIVE_STATES
from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist import locks
from shakenfist import mariadb
from shakenfist.schema.object_filter import ObjectFilterCriteria
from shakenfist.schema.object_types import ObjectType
from shakenfist.util import callstack as util_callstack
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import general as util_general
from shakenfist.util import json as util_json


LOG, _ = logs.setup(__name__)

# Type variable for Pydantic data models used by MariaDB-backed objects
DataT = TypeVar('DataT', bound=BaseModel)


class NoopLock:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        pass

    def __exit__(self, _exception_type, _exception_value, _traceback):
        pass


VERSION_CACHE_MINIMUM = None
VERSION_CACHE_MAXIMUM = None
VERSION_CACHE_AGE = 0


def _maintain_version_cache(max_cache_age):
    global VERSION_CACHE_MINIMUM
    global VERSION_CACHE_MAXIMUM
    global VERSION_CACHE_AGE

    if not VERSION_CACHE_MINIMUM or not VERSION_CACHE_MAXIMUM:
        VERSION_CACHE_MINIMUM = {}
        VERSION_CACHE_MAXIMUM = {}
    elif time.time() - VERSION_CACHE_AGE > max_cache_age:
        VERSION_CACHE_MINIMUM = {}
        VERSION_CACHE_MAXIMUM = {}
    else:
        # Cache up to date
        return

    metrics = {}

    # Only gather metrics for nodes in an active state -- deleted, errored
    # and missing nodes are ignored.
    target_states = sorted(NODE_ACTIVE_STATES)
    # One bulk RPC returns {node_uuid, fqdn, timestamp, metrics} per node,
    # replacing the previous per-node get_node (used only for fqdn) plus a
    # per-node get_node_metrics fan-out. Nodes without a metrics row are simply
    # absent here, exactly as the old loop skipped them.
    for d in mariadb.get_all_node_metrics():  # nopushdown: every node wanted
        if not d.get('metrics'):
            continue
        node_uuid_str = str(d['node_uuid'])
        node_fqdn = d['fqdn']

        state = mariadb.get_state(
            ObjectType.NODE, node_uuid_str)
        if not state or state.value not in target_states:
            continue

        d['metrics']['metrics_age'] = \
            round(time.time() - d.get('timestamp', 0), 2)
        log = LOG.with_fields({
            'node_name': node_fqdn,
            'metrics_age': d['metrics']['metrics_age']
        })

        # Discard very old metrics
        if d['metrics']['metrics_age'] > 300:
            log.warning(
                'Ignoring very old metrics entry '
                'for active node')
            continue

        metrics[node_fqdn] = d['metrics']

    for possible_objname in ObjectType:
        nodes_by_version = defaultdict(list, [])
        node_metric_age = {}
        minimum = inf
        maximum = -1

        for node_name in metrics:
            node_metric_age[f'metrics age {node_name}'] = \
                metrics[node_name]['metrics_age']
            ver = metrics[node_name].get('object_version_%s' % possible_objname)
            if ver:
                minimum = min(minimum, ver)
                maximum = max(maximum, ver)
                nodes_by_version[f'version {ver}'].append(node_name)
            else:
                nodes_by_version['no version reported'].append(node_name)

        VERSION_CACHE_MINIMUM[possible_objname] = minimum
        VERSION_CACHE_MAXIMUM[possible_objname] = maximum

    VERSION_CACHE_AGE = time.time()


def get_minimum_object_version(objname, max_cache_age=300):
    _maintain_version_cache(max_cache_age)
    return VERSION_CACHE_MINIMUM.get(objname, inf)


def get_maximum_object_version(objname, max_cache_age=300):
    _maintain_version_cache(max_cache_age)
    return VERSION_CACHE_MAXIMUM.get(objname, -1)


class DatabaseBackedObject:
    object_type: ClassVar[ObjectType] = ObjectType.UNKNOWN
    initial_version: ClassVar[int] = 1
    current_version: ClassVar[Optional[int]] = None
    upgrade_supported: ClassVar[bool] = True
    state_targets = None

    STATE_INITIAL = 'initial'
    STATE_CREATING = 'creating'
    STATE_CREATED = 'created'
    STATE_DELETED = 'deleted'
    STATE_DELETE_WAIT = 'delete-wait'
    STATE_ERROR = 'error'
    STATE_HARD_DELETED = 'hard-deleted'

    ACTIVE_STATES = {STATE_INITIAL, STATE_CREATING, STATE_CREATED, STATE_ERROR,
                     STATE_DELETE_WAIT}
    HEALTHY_STATES = {STATE_INITIAL, STATE_CREATING, STATE_CREATED}
    INACTIVE_STATES = {}

    VALID_OBJECT_TYPE_RE = re.compile(r'^[a-z_]+$')

    def __init__(self, object_uuid: Union[str, uuid.UUID], version=None,
                 in_memory_only=False):
        # Store UUID as a uuid.UUID object for type safety. Subclasses like
        # Node that use non-UUID identifiers (e.g., hostnames) should override
        # the uuid property to return a string.
        if isinstance(object_uuid, uuid.UUID):
            self.__uuid = object_uuid
        else:
            self.__uuid = uuid.UUID(object_uuid)
        self.__version = version

        self.__in_memory_only = in_memory_only
        if self.__in_memory_only:
            self.__in_memory_values = {}
            # Primary state for in-memory objects lives here, never in
            # MariaDB. An in-memory object that wrote a real object_states
            # row would leak it forever: hard_delete() early-returns for
            # in-memory objects, and state-driven iterators skip objects
            # whose static row is missing, so no cleanup path could ever
            # find the row again (issue 3532).
            self.__in_memory_state = None

        self.log = LOG.with_fields({self.object_type: str(self.__uuid)})

        # Not very good schema rule enforcement for object naming...
        m = self.VALID_OBJECT_TYPE_RE.match(self.object_type)
        if not m:
            self.log.with_fields({
                'object_type': self.object_type
            }).error('Object types must be all lower case with no hyphens!')

        if self.object_type.endswith('s'):
            self.log.with_fields({
                'object_type': self.object_type
            }).error('Object types must be singular!')

    def upgrade(self, static_values):
        if static_values.get('version', self.initial_version) != self.current_version:
            changed = False
            if 'version' not in static_values:
                static_values['version'] = self.initial_version
            starting_version = static_values['version']

            while static_values['version'] != self.current_version:
                step = '_upgrade_step_%d_to_%d' % (static_values['version'],
                                                   static_values['version'] + 1)
                step_func = getattr(self, step)
                if not step_func:
                    raise exceptions.UpgradeException(
                        'Upgrade step %s is missing for object %s'
                        % (step, self.object_type))
                step_func(static_values)
                static_values['version'] += 1
                changed = True

            if changed:
                cluster_minimum = get_minimum_object_version(self.object_type)
                upgrade_log = LOG.with_fields({
                    self.object_type: static_values['uuid'],
                    'start_version': starting_version,
                    'final_version': static_values['version'],
                    'current_version': self.current_version,
                    'cluster_minimum_version': cluster_minimum
                })

                if cluster_minimum == self.current_version:
                    upgrade_log.debug('Online upgrade committed')
                else:
                    upgrade_log.info(
                        'Not committing online upgrade, as not all nodes are updated')

    @classmethod
    def upgrade_pydantic_data(cls, data: DataT, data_class: type[DataT]) -> DataT:
        """Apply version upgrades to a Pydantic data model from MariaDB.

        This method handles lazy upgrades for objects stored in MariaDB using
        immutable Pydantic models. Unlike the etcd upgrade() method which mutates
        a dict in place, this method works with immutable Pydantic models by:

        1. Converting the model to a mutable dict
        2. Applying upgrade steps sequentially (each step mutates the dict)
        3. Creating a new Pydantic model with the upgraded values
        4. Persisting the upgrade if the cluster is ready

        Upgrade steps are defined as class methods named:
            _upgrade_step_N_to_M(cls, values: dict[str, Any]) -> None

        Each step receives a mutable dict and modifies it in place. The step
        should NOT update the 'version' key - that is done automatically.

        Persistence: When the cluster minimum version equals the current version,
        the upgraded data is persisted to MariaDB via _persist_pydantic_upgrade().
        Subclasses must override _persist_pydantic_upgrade() to enable this.

        Args:
            data: The Pydantic model instance to upgrade.
            data_class: The Pydantic model class to construct the result.

        Returns:
            A new Pydantic model instance with upgraded values, or the original
            if no upgrade was needed.

        Raises:
            UpgradeException: If an upgrade step is missing.
        """
        # Check if upgrade is needed
        current_version = getattr(data, 'version', cls.initial_version)
        if current_version == cls.current_version:
            return data

        # Convert immutable Pydantic model to mutable dict for upgrade steps
        values: dict[str, Any] = data.model_dump()

        # Track starting version for logging
        starting_version = values.get('version', cls.initial_version)
        if 'version' not in values:
            values['version'] = cls.initial_version

        # Apply upgrade steps sequentially
        while values['version'] != cls.current_version:
            step_name = '_upgrade_step_%d_to_%d' % (
                values['version'], values['version'] + 1)
            step_func = getattr(cls, step_name, None)
            if step_func is None:
                raise exceptions.UpgradeException(
                    'Upgrade step %s is missing for object %s'
                    % (step_name, cls.object_type))
            step_func(values)
            values['version'] += 1

        # Create the upgraded Pydantic model
        upgraded_data = data_class(**values)

        # Persist the upgrade if the cluster is ready (all nodes at current version)
        cluster_minimum = get_minimum_object_version(cls.object_type)
        upgrade_log = LOG.with_fields({
            cls.object_type: values.get('uuid'),
            'start_version': starting_version,
            'final_version': values['version'],
            'current_version': cls.current_version,
            'cluster_minimum_version': cluster_minimum
        })

        if cluster_minimum == cls.current_version:
            cls._persist_pydantic_upgrade(upgraded_data)
            upgrade_log.debug('Online upgrade committed to MariaDB')
        else:
            upgrade_log.info(
                'Not committing online upgrade, as not all nodes are updated')

        return upgraded_data

    @classmethod
    def _persist_pydantic_upgrade(cls, _data: DataT) -> None:
        """Persist an upgraded Pydantic model to MariaDB.

        Subclasses that use upgrade_pydantic_data() must override this method
        to persist their upgraded data to MariaDB.

        Args:
            _data: The upgraded Pydantic model to persist.
        """
        # Default implementation does nothing. Subclasses override this.
        pass

    @property
    def uuid(self) -> uuid.UUID:
        return self.__uuid

    @property
    def version(self):
        return self.__version

    @property
    def in_memory_only(self):
        return self.__in_memory_only

    def __str__(self):
        return f'{self.object_type}({self.__uuid})'

    def unique_label(self):
        # The uuid is stringified here: several consumers (event 'extra'
        # payloads, tuple comparisons against str uuids, JSON encoding by the
        # log shipper) require a str, and a raw uuid.UUID silently breaks
        # them (issue 3573).
        return (self.object_type, str(self.__uuid))

    def add_event(self, eventtype, message, duration=None, extra=None,
                  suppress_event_logging=False, log_as_error=False):
        if not self.__in_memory_only:
            eventlog.add_event(
                eventtype, self.object_type, self.__uuid, message,
                duration=duration, extra=extra,
                suppress_event_logging=suppress_event_logging,
                log_as_error=log_as_error)

    @classmethod
    def from_db(cls, object_uuid, suppress_failure_audit=False):
        if not object_uuid:
            return None

        static_values = cls._db_get(object_uuid)
        if not static_values:
            if not suppress_failure_audit:
                # A missing object is an ordinary outcome of a lookup -- the
                # caller receives None and branches on it -- so this audit
                # event must not be logged as an error. All 170-odd
                # unsuppressed call sites share this one log signature, which
                # made it untriageable at ERROR (issue 3906). A call site for
                # which a miss has a real consequence logs its own error with
                # its own signature (see _cluster_operation_execute() in
                # daemons/queues/workitem.py).
                eventlog.add_event(
                    EVENT_TYPE_AUDIT, cls.object_type, object_uuid,
                    'attempt to lookup non-existent object',
                    extra={'caller': util_callstack.get_caller(offset=-3)})
            return None

        # Ignore old versions of objects for this check, because namespaces
        # back in the day had neither a version or a uuid.
        if (static_values.get('version') == cls.current_version and
                'uuid' not in static_values):
            LOG.with_fields(static_values).with_fields(
                {
                    'object_type': cls.object_type,
                    'object_uuid': object_uuid,
                    'number_of_static_values': len(static_values)
                }).error('Object with no uuid!')

        return cls(static_values)

    @classmethod
    def from_db_by_ref(cls, object_ref, namespace=None):
        if object_ref and util_general.valid_uuid4(object_ref):
            # Already a valid UUID
            return cls.from_db(object_ref)

        # Check if valid name of an active object
        filters = [partial(state_filter, cls.ACTIVE_STATES)]
        if namespace:
            filters.append(partial(namespace_filter, namespace))

        found_obj = None
        for o in cls.filter(filters):
            if o.name == object_ref:
                if found_obj:
                    raise exceptions.MultipleObjects(
                        'multiple %ss have the name "%s" in namespace "%s"'
                        % (cls.object_type, object_ref, namespace))
                found_obj = o

        return found_obj

    @classmethod
    def filter(cls, filters):
        raise NotImplementedError(
            f'{cls.__name__} must override filter() with a MariaDB '
            'implementation')

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        metadata['uuid'] = object_uuid
        eventlog.add_event(EVENT_TYPE_AUDIT, cls.object_type, object_uuid,
                           'db record created', extra=metadata)

        if 'namespace' in metadata and metadata['namespace']:
            eventlog.add_event(
                EVENT_TYPE_AUDIT, 'namespace', metadata['namespace'],
                'object created', extra=metadata, suppress_event_logging=True)

    @classmethod
    def _db_get(cls, object_uuid):
        raise NotImplementedError(
            f'{cls.__name__} must override _db_get() with a MariaDB '
            'implementation')

    # We need to force in memory values through JSON because some values require
    # a serializer to run to work when we read them.
    def _db_get_attribute(self, attribute, default=None):
        if self.__in_memory_only:
            retval = json.loads(self.__in_memory_values.get(attribute, 'null'))
        else:
            retval = None
        if not retval:
            if default is None:
                return {}
            return default
        return retval

    def _db_get_attributes(self, attribute_prefix):
        if self.__in_memory_only:
            for key in self.__in_memory_values.keys():
                if key.startswith(attribute_prefix):
                    yield key, json.loads(self.__in_memory_values[key])
        # Non-in-memory attribute iteration is handled by subclass
        # overrides that read from MariaDB.

    def _log_attribute_mutation(self, attribute, value):
        """Log an EVENT_TYPE_MUTATE event for an attribute change.

        Some attributes are too frequently changed to have much meaning
        as an event and are excluded.
        """
        if (self.object_type, attribute) not in [('node', 'blobs'),
                                                 ('node', 'observed'),
                                                 ('blob', 'last_used')]:
            if isinstance(value, State):
                event_values = value.obj_dict()
            elif isinstance(value, dict):
                event_values = value.copy()
            else:
                event_values = {'value': value}
            event_values['attribute'] = attribute
            self.add_event(EVENT_TYPE_MUTATE, 'set attribute', extra=event_values)

    def _db_set_attribute(self, attribute, value):
        self._log_attribute_mutation(attribute, value)

        if self.__in_memory_only:
            self.__in_memory_values[attribute] = util_json.json_dump(value)
        else:
            LOG.warning(
                f'Base class _db_set_attribute called for '
                f'{self.object_type}/{attribute} — subclass should override')

    def _db_delete_attribute(self, attribute):
        if self.__in_memory_only and attribute in self.__in_memory_values:
            del self.__in_memory_values[attribute]
        else:
            LOG.warning(
                f'Base class _db_delete_attribute called for '
                f'{self.object_type}/{attribute} — subclass should override')

    def _add_item_in_attribute_list(self, listname, item):
        with self.get_lock_attr(listname, 'Add %s' % listname):
            items = self._db_get_attribute(listname).get(listname, [])
            if item not in items:
                items.append(item)
                self._db_set_attribute(listname, {
                    listname: items,
                    'initialized': True
                })

    def _remove_item_in_attribute_list(self, listname, item):
        with self.get_lock_attr(listname, 'Remove %s' % listname):
            items = self._db_get_attribute(listname).get(listname, [])
            if item in items:
                items.remove(item)
                self._db_set_attribute(listname, {
                    listname: items,
                    'initialized': True
                })

    def get_lock(self, subtype=None, op=None, global_scope=True,
                 timeout=60):
        # There is no point locking in-memory objects
        if self.in_memory_only:
            return NoopLock()

        if not global_scope:
            return util_concurrency.NodeLock(f'{self.object_type}-{self.uuid}')

        return locks.ClusterLock(
            self.object_type, subtype, str(self.uuid), log_ctx=self.log, op=op,
            timeout=timeout)

    def get_lock_attr(self, name, op, global_scope=True, timeout=10):
        # There is no point locking in-memory objects
        if self.in_memory_only:
            return NoopLock()

        if not global_scope:
            return util_concurrency.NodeLock(
                f'{self.object_type}-{self.uuid}-{name}')

        return locks.ClusterLock(
            'attribute/%s' % self.object_type, str(self.__uuid), name, op=op,
            timeout=timeout, log_ctx=self.log)

    # Properties common to all objects which are routed to attributes
    @property
    def state(self):
        return self._state_read(state_attribute_name='state')

    def _state_read(self, state_attribute_name='state'):
        # Primary state is stored in MariaDB, except for in-memory only
        # objects, which must never touch the database
        if state_attribute_name == 'state':
            if self.__in_memory_only:
                if self.__in_memory_state is not None:
                    return self.__in_memory_state
                return State(value=None, update_time=0)

            state = mariadb.get_state(self.object_type, str(self.uuid))
            if state is not None:
                return state
            return State(value=None, update_time=0)

        # Secondary state attributes (like 'power_state') are in etcd
        db_data = self._db_get_attribute(state_attribute_name)
        if not db_data:
            return State(value=None, update_time=0)
        return State(**db_data)

    def _state_update(self, new_value, skip_transition_validation=False,
                      state_attribute_name='state', message=None):
        # Is this a change?
        orig = self._state_read(state_attribute_name=state_attribute_name)
        if orig.value == new_value:
            return

        # Only standard states have validation right now
        if state_attribute_name == 'state':
            if orig.value == self.STATE_DELETED and self.object_type != ObjectType.NODE:
                LOG.with_fields(
                    {
                        'uuid': self.uuid,
                        'object_type': self.object_type,
                        'original state': orig,
                        'new state': new_value
                    }).warn('Objects do not undelete')
                raise exceptions.InvalidStateException(
                    'Invalid state change from %s to %s for '
                    'object=%s uuid=%s',
                    orig.value, new_value, self.object_type, self.uuid)

            # Ensure state change is valid
            if not skip_transition_validation:
                if not self.state_targets:
                    raise exceptions.NoStateTransitionsDefined(
                        self.object_type)

                if new_value not in self.state_targets.get(orig.value, []):
                    raise exceptions.InvalidStateException(
                        'Invalid state change from %s to %s for '
                        'object=%s uuid=%s',
                        orig.value, new_value, self.object_type, self.uuid)

        new_state = State(value=new_value, update_time=time.time(), message=message)

        # Primary state is stored in MariaDB, except for in-memory only
        # objects, which must never touch the database
        if state_attribute_name == 'state':
            if self.__in_memory_only:
                self.__in_memory_state = new_state
                return

            if not mariadb.set_state(self.object_type, str(self.uuid), new_state):
                LOG.with_fields({
                    'object_type': self.object_type,
                    'object_uuid': str(self.uuid),
                    'new_state': new_value,
                }).error('Failed to write state to MariaDB')
                raise RuntimeError(
                    f'Failed to write state {new_value} for '
                    f'{self.object_type}/{self.uuid} to MariaDB'
                )
            self._log_attribute_mutation('state', new_state)
        else:
            # Secondary state attributes (like 'power_state') go to etcd
            self._db_set_attribute(state_attribute_name, new_state)

    @state.setter
    def state(self, new_value):
        self._state_update(new_value)

    @property
    def error(self):
        # The error message is stored as the message on the object's state
        # row, which every object type persists (issue 3899: the previous
        # 'error' attribute write was silently discarded for every type
        # except Instance). It is only meaningful while the object is in an
        # error state; _state_update() clears it on the next transition.
        s = self.state
        if s.value and s.value.endswith('error'):
            return s.message
        return None

    @error.setter
    def error(self, msg):
        s = self.state
        if msg and not s.value.endswith('error'):
            raise exceptions.InvalidStateException(
                'Object not in error state (state=%s, object=%s)'
                % (s, self.object_type))
        if s.message == msg:
            return

        new_state = State(value=s.value, update_time=time.time(), message=msg)
        if self.__in_memory_only:
            self.__in_memory_state = new_state
        elif not mariadb.set_state(self.object_type, str(self.uuid), new_state):
            raise RuntimeError(
                f'Failed to write error message for '
                f'{self.object_type}/{self.uuid} to MariaDB')
        self._log_attribute_mutation('error', {'message': msg})

    @property
    def metadata(self):
        if self.in_memory_only:
            return self._db_get_attribute('metadata', {})
        obj_meta = mariadb.get_object_metadata(
            self.object_type, str(self.uuid))
        if obj_meta and obj_meta.metadata is not None:
            return obj_meta.metadata
        return {}

    def add_metadata_key(self, key, value):
        with self.get_lock_attr('metadata', 'Add metadata key'):
            md = self.metadata
            md[key] = value
            if self.in_memory_only:
                self._db_set_attribute('metadata', md)
            else:
                mariadb.set_metadata(
                    self.object_type, str(self.uuid), md)

    def remove_metadata_key(self, key):
        with self.get_lock_attr('metadata', 'Remove metadata key'):
            md = self.metadata
            if key in md:
                del md[key]
                if self.in_memory_only:
                    self._db_set_attribute('metadata', md)
                else:
                    mariadb.set_metadata(
                        self.object_type, str(self.uuid), md)

    def _external_view(self):
        # Import here to avoid circular imports during module loading
        from shakenfist.schema.external_view import BaseExternalView

        # Phase 1: Fields handled by Pydantic model (grows over time)
        # The model handles transformations like State -> state value string
        partial = BaseExternalView(
            uuid=self.uuid,
            state=self.state,
            version=self.version,
            metadata=self.metadata
        )
        out = partial.model_dump()

        # Phase 2: Fields not yet migrated to Pydantic (shrinks over time)
        # Currently empty for base class - subclasses add their own fields

        return out

    def hard_delete(self):
        mariadb.delete_state(self.object_type, str(self.uuid))
        mariadb.delete_object_metadata(self.object_type, str(self.uuid))
        mariadb.delete_object_events(self.object_type, str(self.uuid))
        self.add_event(EVENT_TYPE_AUDIT, 'hard deleted object')


class DatabaseBackedObjectWithOperations(DatabaseBackedObject):
    @property
    def last_cluster_operation(self):
        if self.in_memory_only:
            return None
        latest = mariadb.get_latest_cluster_operation_target(
            self.object_type, str(self.uuid))
        if latest is not None:
            return {
                'op_type': latest.operation_type,
                'op_uuid': latest.operation_uuid
            }
        return None

    def has_pending_cluster_operation(self) -> bool:
        """True if any in-flight cluster operation targets this object.

        Replaces the legacy pattern of reading ``last_cluster_operation``
        and inspecting the embedded operation's state. That pattern was
        racy: a later terminal op against the same object would mask an
        earlier in-flight op. This query inspects every target row.
        """
        if self.in_memory_only:
            return False
        return mariadb.has_pending_cluster_operation_target(
            self.object_type, str(self.uuid))

    def _set_last_cluster_operation(self, op_type, op_uuid):
        if not self.in_memory_only:
            success = mariadb.create_cluster_operation_target(
                operation_uuid=str(op_uuid),
                operation_type=str(op_type),
                target_object_type=self.object_type,
                target_uuid=str(self.uuid),
                created_at=time.time()
            )
            if not success:
                LOG.with_fields({
                    'object_type': self.object_type,
                    'object_uuid': str(self.uuid),
                    'op_type': str(op_type),
                    'op_uuid': str(op_uuid),
                }).error(
                    'Failed to write cluster operation target '
                    'to MariaDB'
                )
                raise RuntimeError(
                    f'Failed to write cluster operation target for '
                    f'{self.object_type}/{self.uuid} op {op_type}/{op_uuid}'
                )

    def get_cluster_operations(self, outstanding_only=True):
        # All ``from_db`` lookups here are best-effort: an op can be
        # hard-deleted between when we read its target row (or its
        # depends_on / runs_after reference) and when we try to load
        # it. The "non-existent object" audit event is therefore
        # suppressed for these lookups — treating the absent op as
        # not-outstanding is the correct behaviour.
        last_op = self.last_cluster_operation
        if not last_op:
            return []
        if not last_op.get('op_type'):
            return []

        op = get_object_class(last_op.get('op_type')).from_db(
            last_op.get('op_uuid'), suppress_failure_audit=True)
        if not op:
            return []
        if outstanding_only and not op.is_outstanding():
            return []

        outstanding = [op]

        for dep in op.depends_on:
            dep_op = get_object_class(dep['op_type']).from_db(
                dep['op_uuid'], suppress_failure_audit=True)
            if not dep_op:
                continue
            if outstanding_only and not dep_op.is_outstanding():
                continue
            outstanding.append(dep_op)

        for dep in op.runs_after:
            dep_op = get_object_class(dep['op_type']).from_db(
                dep['op_uuid'], suppress_failure_audit=True)
            if not dep_op:
                continue
            if outstanding_only and not dep_op.is_outstanding():
                continue
            outstanding.append(dep_op)

        return outstanding

    def hard_delete(self):
        mariadb.delete_cluster_operation_targets_for_object(
            self.object_type, str(self.uuid))
        super().hard_delete()


class DatabaseBackedObjectIterator:
    def __init__(self, filters=None, prefilter=None, namespace=None,
                 suppress_failure_audit=False):
        self.filters = filters or []
        self.prefilter = prefilter
        self.namespace = namespace
        self.suppress_failure_audit = suppress_failure_audit

    def _resolve_prefilter_to_states(self) -> set[str]:
        if self.prefilter == 'active':
            target_states = self.base_object.ACTIVE_STATES
        elif self.prefilter == 'deleted':
            target_states = [DatabaseBackedObject.STATE_DELETED]
        elif self.prefilter == 'healthy':
            target_states = self.base_object.HEALTHY_STATES
        elif self.prefilter == 'inactive':
            target_states = self.base_object.INACTIVE_STATES
        elif self.prefilter is None:
            # No prefilter — return all non-deleted objects
            target_states = self.base_object.ACTIVE_STATES
        else:
            raise exceptions.InvalidObjectPrefilter(self.prefilter)
        return set(target_states)

    def _find(self, criteria):
        """Default: two-step get_objects_by_state + _db_get hydration.

        Subclasses SHOULD override with a single mariadb.find_* call
        when the object has a Find* RPC available. The default exists
        to keep subclasses that inherit the iterator (IPAMs,
        AgentOperations) working until they get their own overrides.

        A tier-wide failure -- DatabaseUnavailable, raised once
        _grpc_call has spent its retry budget -- deliberately
        propagates to the caller rather than being caught here.
        Catching it would turn a read that did not happen into an
        iteration that found nothing, which is the #3638 hazard rebuilt
        one layer up and in the widest consumer of the database in the
        tree. A caller that genuinely tolerates an unreadable database
        catches it explicitly, the way the cluster daemon's cleanup
        does.
        """
        uuids = mariadb.get_objects_by_state(
            self.base_object.object_type, criteria.states or [])
        if uuids is None:
            # The per-reply failure shape (RESOURCE_EXHAUSTED, and
            # anything else that is not worth a retry) still truncates
            # to an empty iteration here. That is tolerable only
            # because every iterator caller today iterates the result
            # rather than complementing it; it is named in phase 1 of
            # PLAN-grpc-bounded-replies.md so it stays a recorded
            # judgement rather than an inherited default.
            LOG.warning(
                'get_objects_by_state returned None for '
                f'{self.base_object.object_type}')
            return
        for objuuid in uuids:
            data = self.base_object._db_get(objuuid)
            if data is None:
                continue
            # Default doesn't know how to push namespace to SQL,
            # so filter in Python. Subclasses that override _find
            # for fast SQL paths handle namespace there.
            if criteria.namespace is not None:
                if isinstance(data, dict):
                    ns = data.get('namespace')
                else:
                    ns = getattr(data, 'namespace', None)
                if ns != criteria.namespace:
                    continue
            yield data

    def _to_static_values(self, data):
        return data

    def get_iterator(self):
        target_states = self._resolve_prefilter_to_states()
        criteria_namespace = (
            self.namespace
            if self.namespace and self.namespace != 'system'
            else None)
        criteria = ObjectFilterCriteria(
            states=list(target_states),
            namespace=criteria_namespace,
        )
        for data in self._find(criteria):
            if isinstance(data, dict):
                objuuid = data.get('uuid')
            else:
                objuuid = data.uuid
            yield str(objuuid), self._to_static_values(data)

    def apply_filters(self, o):
        for f in self.filters:
            if not f(o):
                return None

        return o


def state_filter(states, o):
    return o.state.value in states


def state_age_filter(delay, o):
    return (time.time() - o.state.update_time) > delay


def namespace_filter(namespace, o):
    if namespace == 'system':
        return True
    return o.namespace == namespace


# Import Pydantic State from schema - this replaces the old class below.
# Placed here to avoid circular imports during module loading.
from shakenfist.schema.object_state import State  # noqa: E402
