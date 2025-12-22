import json
import time
from collections import defaultdict
from functools import partial
from math import inf
import re

from etcd3gw.lock import Lock
from shakenfist_utilities import logs  # noreorder

from shakenfist import constants
from shakenfist.constants import get_object_class
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.util import callstack as util_callstack
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import general as util_general
from shakenfist.util import json as util_json


LOG, _ = logs.setup(__name__)


class NoopLock(Lock):
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

    # Ignore metrics for deleted nodes, but include nodes in an error state
    # as they may return.
    target_states = [DatabaseBackedObject.STATE_INITIAL,
                     DatabaseBackedObject.STATE_CREATED,
                     'degraded']
    for node_key, n in etcd.get_all('node', None):
        # node_key is the full etcd path, extract the node name (fqdn)
        node_name = node_key.split('/')[-1]
        state_data = etcd.get('attribute/node', node_name, 'state')
        if not state_data or state_data.get('value') not in target_states:
            continue
        d = etcd.get('metrics', node_name, None)
        if not d:
            continue

        d['metrics']['metrics_age'] = \
            round(time.time() - d.get('timestamp', 0), 2)
        log = LOG.with_fields({
            'node_name': node_name,
            'metrics_age': d['metrics']['metrics_age']
        })

        # Discard very old metrics
        if d['metrics']['metrics_age'] > 300:
            log.warning('Ignoring very old metrics entry for active node')
            continue

        metrics[node_name] = d['metrics']
        log.debug('Considering metrics entry')

    for possible_objname in constants.OBJECT_NAMES:
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
    object_type = 'unknown'
    initial_version = 1
    current_version = None
    upgrade_supported = True
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

    def __init__(self, object_uuid, version=None, in_memory_only=False):
        self.__uuid = object_uuid
        self.__version = version

        self.__in_memory_only = in_memory_only
        if self.__in_memory_only:
            self.__in_memory_values = {}

        self.log = LOG.with_fields({self.object_type: self.__uuid})

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
                    etcd.put(self.object_type, None, static_values.get('uuid'),
                             static_values)
                    upgrade_log.debug('Online upgrade committed')
                else:
                    upgrade_log.info(
                        'Not committing online upgrade, as not all nodes are updated')

    @property
    def uuid(self):
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
        return (self.object_type, self.__uuid)

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
                eventlog.add_event(
                    EVENT_TYPE_AUDIT, cls.object_type, object_uuid,
                    'attempt to lookup non-existent object',
                    extra={'caller': util_callstack.get_caller(offset=-3)},
                    log_as_error=True)
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
        for _, o in etcd.get_all(cls.object_type, None):
            obj = cls(o)
            if all([f(obj) for f in filters]):
                yield obj

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        metadata['uuid'] = object_uuid
        etcd.create(cls.object_type, None, object_uuid, metadata)
        eventlog.add_event(EVENT_TYPE_AUDIT, cls.object_type, object_uuid,
                           'db record created', extra=metadata)

        if 'namespace' in metadata and metadata['namespace']:
            eventlog.add_event(
                EVENT_TYPE_AUDIT, 'namespace', metadata['namespace'],
                'object created', extra=metadata, suppress_event_logging=True)

    @classmethod
    def _db_get(cls, object_uuid):
        o = etcd.get(cls.object_type, None, object_uuid)
        if not o:
            return None

        if o.get('version', 0) != cls.current_version:
            if not cls.upgrade_supported:
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - {cls.object_type}: {o}')
        return o

    @classmethod
    def _upgrade_metadata_to_attribute(cls, object_uuid):
        md = etcd.get('metadata', cls.object_type, object_uuid)
        if md:
            etcd.put('attribute/%s' % cls.object_type, object_uuid, 'metadata', md)
            etcd.delete('metadata', cls.object_type, object_uuid)

    # We need to force in memory values through JSON because some values require
    # a serializer to run to work when we read them.
    def _db_get_attribute(self, attribute, default=None):
        if self.__in_memory_only:
            retval = json.loads(self.__in_memory_values.get(attribute, 'null'))
        else:
            retval = etcd.get(f'attribute/{self.object_type}',
                              self.__uuid, attribute)
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
        else:
            for key, data in etcd.get_all('attribute/%s' % self.object_type,
                                          self.__uuid, prefix=attribute_prefix):
                yield key, data

    def _db_set_attribute(self, attribute, value):
        # Some attributes are simply too frequently changed to have much meaning
        # as an event.
        if (self.object_type, attribute) not in [('node', 'blobs'),
                                                 ('node', 'observed'),
                                                 ('blob', 'ref_count'),
                                                 ('blob', 'last_used')]:
            # Coerce the value into a dictionary.
            if isinstance(value, State):
                event_values = value.obj_dict()
            elif isinstance(value, dict):
                event_values = value.copy()
            else:
                event_values = {'value': value}

            # Add the attribute we're setting to the event so we're not confused
            # later.
            event_values['attribute'] = attribute
            self.add_event(EVENT_TYPE_MUTATE, 'set attribute', extra=event_values)

        if self.__in_memory_only:
            self.__in_memory_values[attribute] = util_json.json_dump(value)
        else:
            etcd.put('attribute/%s' % self.object_type,
                     self.__uuid, attribute, value)

    def _db_delete_attribute(self, attribute):
        if self.__in_memory_only and attribute in self.__in_memory_values:
            del self.__in_memory_values[attribute]
        else:
            etcd.delete(
                'attribute/%s' % self.object_type, self.__uuid, attribute)

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
                 timeout=constants.ETCD_ATTEMPT_TIMEOUT):
        # There is no point locking in-memory objects
        if self.in_memory_only:
            return NoopLock()

        if not global_scope:
            return util_concurrency.NodeLock(f'{self.object_type}-{self.uuid}')

        return etcd.ClusterLock(
            self.object_type, subtype, self.uuid, log_ctx=self.log, op=op,
            timeout=timeout)

    def get_lock_attr(self, name, op, global_scope=True, timeout=10):
        # There is no point locking in-memory objects
        if self.in_memory_only:
            return NoopLock()

        if not global_scope:
            return util_concurrency.NodeLock(
                f'{self.object_type}-{self.uuid}-{name}')

        return etcd.ClusterLock(
            'attribute/%s' % self.object_type, self.__uuid, name, op=op,
            timeout=timeout, log_ctx=self.log)

    # Properties common to all objects which are routed to attributes
    @property
    def state(self):
        return self._state_read(state_attribute_name='state')

    def _state_read(self, state_attribute_name='state'):
        # For the primary 'state' attribute, try MariaDB first if configured
        if state_attribute_name == 'state' and mariadb.is_configured():
            state = mariadb.get_state(self.object_type, self.uuid)
            if state is not None:
                return state
            # Fall through to etcd if not found in MariaDB

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

        # NOTE(mikal): I'm adding this lock back in, even though I don't want to,
        # because until we can do this entire update (the state change and the
        # cache update) in a single transaction its going to continue to cause
        # me problems. This all needs a rethink.
        with self.get_lock_attr(state_attribute_name, 'update'):
            # Only standard states have validation right now
            if state_attribute_name == 'state':
                if orig.value == self.STATE_DELETED and self.object_type != 'node':
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

            new_state = State(value=new_value, update_time=time.time(),
                              message=message)

            # Write to MariaDB for the primary state attribute
            if state_attribute_name == 'state' and mariadb.is_configured():
                mariadb.set_state(self.object_type, self.uuid, new_state)

            # Always write to etcd for backwards compatibility during transition
            self._db_set_attribute(state_attribute_name, new_state)

    @state.setter
    def state(self, new_value):
        self._state_update(new_value)

    @property
    def error(self):
        db_data = self._db_get_attribute('error', {'message': None})
        return db_data.get('message')

    @error.setter
    def error(self, msg):
        if msg:
            s = self.state
            if not s.value.endswith('error'):
                raise exceptions.InvalidStateException(
                    'Object not in error state (state=%s, object=%s)'
                    % (s, self.object_type))
        self._db_set_attribute('error', {'message': msg})

    @property
    def metadata(self):
        return self._db_get_attribute('metadata', {})

    def add_metadata_key(self, key, value):
        with self.get_lock_attr('metadata', 'Add metadata key'):
            md = self.metadata
            md[key] = value
            self._db_set_attribute('metadata', md)

    def remove_metadata_key(self, key):
        with self.get_lock_attr('metadata', 'Remove metadata key'):
            md = self.metadata
            if key in md:
                del md[key]
                self._db_set_attribute('metadata', md)

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
        etcd.delete(self.object_type, None, self.uuid)
        etcd.delete_all('attribute/%s' % self.object_type, self.uuid)
        self.add_event(EVENT_TYPE_AUDIT, 'hard deleted object')


class DatabaseBackedObjectWithOperations(DatabaseBackedObject):
    @property
    def last_cluster_operation(self):
        return self._db_get_attribute('last_cluster_operation')

    def set_last_cluster_operation(self, op_type, op_uuid):
        self._db_set_attribute(
            'last_cluster_operation',
            {
                'op_type': op_type,
                'op_uuid': op_uuid
            }
        )

    def get_cluster_operations(self, outstanding_only=True):
        last_op = self.last_cluster_operation
        if not last_op:
            return []
        if not last_op.get('op_type'):
            return []

        op = get_object_class(last_op.get('op_type')).from_db(
            last_op.get('op_uuid'))
        if not op:
            return []
        if outstanding_only and not op.is_outstanding():
            return []

        outstanding = [op]

        for dep in op.depends_on:
            dep_op = get_object_class(dep['op_type']).from_db(dep['op_uuid'])
            if not dep_op:
                continue
            if outstanding_only and not dep_op.is_outstanding():
                continue
            outstanding.append(dep_op)

        for dep in op.runs_after:
            dep_op = get_object_class(dep['op_type']).from_db(dep['op_uuid'])
            if not dep_op:
                continue
            if outstanding_only and not dep_op.is_outstanding():
                continue
            outstanding.append(dep_op)

        return outstanding


class DatabaseBackedObjectIterator:
    def __init__(self, filters, prefilter=None, suppress_failure_audit=False):
        self.filters = filters
        self.prefilter = prefilter
        self.suppress_failure_audit = suppress_failure_audit

    def get_iterator(self):
        if not self.prefilter:
            for objuuid, objdata in etcd.get_all(self.base_object.object_type, None):
                yield objuuid, objdata
            return

        if self.prefilter == 'active':
            target_states = self.base_object.ACTIVE_STATES
        elif self.prefilter == 'deleted':
            target_states = [DatabaseBackedObject.STATE_DELETED]
        elif self.prefilter == 'healthy':
            target_states = self.base_object.HEALTHY_STATES
        elif self.prefilter == 'inactive':
            target_states = self.base_object.INACTIVE_STATES
        else:
            raise exceptions.InvalidObjectPrefilter(self.prefilter)

        # We fetch all the results in a block here before we yield them, because
        # if the caller is slow to iterate they can end up with inconsistent
        # values as objects shift state underneath them (for example an active
        # instance shifting from created to delete-wait while you're iterating).
        results = []
        for objkey, static_values in etcd.get_all(
                self.base_object.object_type, None):
            # objkey is the full etcd path like /sf/node/node1_net, extract
            # the object identifier (last component of the path)
            objuuid = objkey.split('/')[-1]
            state_data = etcd.get(
                f'attribute/{self.base_object.object_type}', objuuid, 'state')
            if state_data and state_data.get('value') in target_states:
                results.append((objkey, static_values))
        for objkey, static_values in results:
            yield objkey, static_values

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
