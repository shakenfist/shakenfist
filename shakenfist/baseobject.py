from functools import partial
import json
import time

from shakenfist import constants
from shakenfist import db
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist.util import general as util_general


LOG, _ = logutil.setup(__name__)


class DatabaseBackedObject(object):
    object_type = 'unknown'
    current_version = None
    upgrade_supported = False
    state_targets = None

    STATE_INITIAL = 'initial'
    STATE_CREATING = 'creating'
    STATE_CREATED = 'created'
    STATE_DELETED = 'deleted'
    STATE_DELETE_WAIT = 'delete-wait'
    STATE_ERROR = 'error'

    ACTIVE_STATES = set([STATE_INITIAL,
                         STATE_CREATING,
                         STATE_CREATED,
                         STATE_ERROR,
                         STATE_DELETE_WAIT
                         ])

    def __init__(self, object_uuid, version=None, in_memory_only=False):
        self.__uuid = object_uuid
        self.__version = version

        self.__in_memory_only = in_memory_only
        if self.__in_memory_only:
            self.__in_memory_values = {}

        self.log = LOG.with_object(self)

    @property
    def uuid(self):
        return self.__uuid

    @property
    def version(self):
        return self.__version

    def __str__(self):
        return '%s(%s)' % (self.object_type, self.__uuid)

    def unique_label(self):
        return (self.object_type, self.__uuid)

    # Shim to track what hasn't been converted to the new style yet
    def add_event2(self, message, duration=None, extra=None, suppress_event_logging=False):
        if not self.__in_memory_only:
            eventlog.add_event2(
                self.object_type, self.__uuid, message, duration=duration,
                extra=extra, suppress_event_logging=suppress_event_logging)

    @classmethod
    def from_db(cls, object_uuid):
        if not object_uuid:
            return None

        static_values = cls._db_get(object_uuid)
        if not static_values:
            return None

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
        eventlog.add_event2(cls.object_type, object_uuid, 'db record created',
                            extra=metadata)

    @classmethod
    def _db_get(cls, object_uuid):
        o = etcd.get(cls.object_type, None, object_uuid)
        if not o:
            return None

        if o.get('version', 0) != cls.current_version:
            if not cls.upgrade_supported:
                raise exceptions.BadObjectVersion(
                    'Unsupported object version - %s: %s' % (cls.object_type, o))
        return o

    # We need to force in memory values through JSON because some values require
    # a serializer to run to work when we read them.
    def _db_get_attribute(self, attribute):
        if self.__in_memory_only:
            retval = json.loads(self.__in_memory_values.get(attribute, 'null'))
        else:
            retval = etcd.get('attribute/%s' % self.object_type,
                              self.__uuid, attribute)
        if not retval:
            return {}
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
        if (self.object_type, attribute) not in [('node', 'blobs'), ('node', 'observed')]:
            # Coerce the value into a dictionary.
            if type(value) is State:
                event_values = value.obj_dict()
            elif type(value) is dict:
                event_values = value.copy()
            else:
                event_values = {'value': value}

            # Add the attribute we're setting to the event so we're not confused
            # later.
            event_values['attribute'] = attribute
            self.add_event2('set attribute', extra=event_values)

        if self.__in_memory_only:
            self.__in_memory_values[attribute] = json.dumps(
                value, indent=4, sort_keys=True, cls=etcd.JSONEncoderCustomTypes)
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

    def get_lock(self, subtype=None, ttl=60, relatedobjects=None, log_ctx=None,
                 op=None, timeout=constants.ETCD_ATTEMPT_TIMEOUT):
        if not log_ctx:
            log_ctx = self.log
        return db.get_lock(self.object_type, subtype, self.uuid, ttl=ttl,
                           relatedobjects=relatedobjects, log_ctx=log_ctx,
                           op=op, timeout=timeout)

    def get_lock_attr(self, name, op):
        return db.get_lock('attribute/%s' % self.object_type,
                           self.__uuid, name, op=op)

    # Properties common to all objects which are routed to attributes
    @property
    def state(self):
        db_data = self._db_get_attribute('state')
        if not db_data:
            return State(None, 0)
        return State(**db_data)

    @state.setter
    def state(self, new_value):
        with self.get_lock_attr('state', 'State update'):
            orig = self.state

            if orig.value == new_value:
                return

            if orig.value == self.STATE_DELETED:
                LOG.with_fields(
                    {
                        'uuid': self.uuid,
                        'object_type': self.object_type,
                        'original state': orig,
                        'new state': new_value
                    }).warn('Objects do not undelete')
                raise exceptions.InvalidStateException(
                    'Invalid state change from %s to %s for object=%s uuid=%s',
                    orig.value, new_value, self.object_type, self.uuid)

            # Ensure state change is valid
            if not self.state_targets:
                raise exceptions.NoStateTransitionsDefined(
                    self.object_type)

            if new_value not in self.state_targets.get(orig.value, []):
                raise exceptions.InvalidStateException(
                    'Invalid state change from %s to %s for object=%s uuid=%s',
                    orig.value, new_value, self.object_type, self.uuid)

            new_state = State(new_value, time.time())
            self._db_set_attribute('state', new_state)

    @property
    def error(self):
        db_data = self._db_get_attribute('error')
        if not db_data:
            return None
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

    def hard_delete(self):
        etcd.delete(self.object_type, None, self.uuid)
        etcd.delete_all('attribute/%s' % self.object_type, self.uuid)
        db.delete_metadata(self.object_type, self.uuid)
        self.add_event2('hard deleted object')


class DatabaseBackedObjectIterator(object):
    def __init__(self, filters):
        self.filters = filters

    def apply_filters(self, o):
        for f in self.filters:
            if not f(o):
                return None

        return o


def state_filter(states, o):
    return o.state.value in states


# Do not use these filters for instances or nodes, use the more
# specific ones instead
active_states_filter = partial(
    state_filter, DatabaseBackedObject.ACTIVE_STATES)


def state_age_filter(delay, o):
    return (time.time() - o.state.update_time) > delay


def namespace_filter(namespace, o):
    if namespace == 'system':
        return True
    return o.namespace == namespace


class State(object):
    def __init__(self, value, update_time):
        self.__value = value
        self.__update_time = update_time

    def __repr__(self):
        return 'State(' + str(self.obj_dict()) + ')'

    def __eq__(self, other):
        return self.__hash__() == other.__hash__()

    def __hash__(self):
        return hash(str(self.obj_dict()))

    @property
    def value(self):
        return self.__value

    @property
    def update_time(self):
        return self.__update_time

    def obj_dict(self):
        return {
            'value': self.__value,
            'update_time': self.__update_time,
        }
