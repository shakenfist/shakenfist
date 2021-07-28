
from functools import partial
import time

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
    STATE_ERROR = 'error'

    def __init__(self, object_uuid, version=None):
        self.__uuid = object_uuid
        self.__version = version

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
        db.add_event(
            self.object_type, self.__uuid, operation, phase, duration, msg)

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        metadata['uuid'] = object_uuid
        etcd.create(cls.object_type, None, object_uuid, metadata)

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

    def _db_get_attribute(self, attribute):
        retval = etcd.get('attribute/%s' % self.object_type,
                          self.__uuid, attribute)
        if not retval:
            return {}
        return retval

    def _db_get_attributes(self, attribute_prefix):
        for key, data in etcd.get_all('attribute/%s' % self.object_type,
                                      self.__uuid, prefix=attribute_prefix):
            yield key, data

    def _db_set_attribute(self, attribute, value):
        etcd.put('attribute/%s' % self.object_type,
                 self.__uuid, attribute, value)

    def _db_delete_attribute(self, attribute):
        etcd.delete('attribute/%s' % self.object_type, self.__uuid, attribute)

    def get_lock(self, subtype=None, ttl=60, relatedobjects=None, log_ctx=None,
                 op=None):
        if not log_ctx:
            log_ctx = self.log
        return db.get_lock(self.object_type, subtype, self.uuid, ttl=ttl,
                           relatedobjects=relatedobjects, log_ctx=log_ctx,
                           op=op)

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
            LOG.with_fields(
                {
                    'uuid': self.uuid,
                    'object_type': self.object_type,
                    'original state': orig,
                    'new state': new_value,
                    'targets': self.state_targets
                }).debug('Verifying state transition')

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
                   DatabaseBackedObject.STATE_ERROR])
inactive_states_filter = partial(
    state_filter, [DatabaseBackedObject.STATE_DELETED])


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
