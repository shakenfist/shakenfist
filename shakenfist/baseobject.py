
from functools import partial
import json
import time

from shakenfist import constants
from shakenfist import db
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import logutil


LOG, _ = logutil.setup(__name__)


class DatabaseBackedObject(object):
    object_type = 'unknown'
    current_version = None
    state_targets = None

    STATE_INITIAL = 'initial'
    STATE_CREATING = 'creating'
    STATE_CREATED = 'created'
    STATE_DELETED = 'deleted'
    STATE_DELETE_WAIT = 'delete_wait'
    STATE_ERROR = 'error'

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

    def add_event(self, operation, phase, duration=None, msg=None):
        if not self.__in_memory_only:
            db.add_event(
                self.object_type, self.__uuid, operation, phase, duration, msg)

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        # NOTE(mikal): the copy() here is important. The logging framework mucks
        # with the metadata dictionary it is passed in such a way that object
        # creation breaks. Only give it a copy!
        LOG.with_fields(metadata.copy()).with_field(
            cls.object_type, object_uuid).debug('Object created')
        metadata['uuid'] = object_uuid
        etcd.create(cls.object_type, None, object_uuid, metadata)

        db.add_event(
            cls.object_type, object_uuid, 'db record created', None, None, None)

    @classmethod
    def _db_get(cls, object_uuid):
        # NOTE(mikal): we don't do upgrades inflight. They are assumed to have
        # been done as part of the upgrade process.
        o = etcd.get(cls.object_type, None, object_uuid)
        if not o:
            return None

        if o.get('version', 0) != cls.current_version:
            raise exceptions.BadObjectVersion(
                'Unknown version - %s: %s' % (cls.object_type, o))
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
            etcd.delete('attribute/%s' %
                        self.object_type, self.__uuid, attribute)

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
            self.add_event('state changed',
                           '%s -> %s' % (orig.value, new_value))
            self.error = None

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
    state_filter, [DatabaseBackedObject.STATE_INITIAL,
                   DatabaseBackedObject.STATE_CREATING,
                   DatabaseBackedObject.STATE_CREATED,
                   DatabaseBackedObject.STATE_ERROR,
                   DatabaseBackedObject.STATE_DELETE_WAIT])


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
