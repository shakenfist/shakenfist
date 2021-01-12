
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

    def __init__(self, object_uuid, version=None, error_msg=None):
        self.__uuid = object_uuid
        self.__version = version
        self.__error_msg = error_msg

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
            LOG.withField(cls.object_type, object_uuid).error('Object missing')
            return None

        if o.get('version', 0) != cls.current_version:
            raise exceptions.BadObjectVersion(
                'Unknown version - %s: %s' % (cls.object_type, o))
        return o

    def _db_get_attribute(self, attribute):
        retval = etcd.get('attribute/%s' % self.object_type, self.__uuid, attribute)
        if not retval:
            return {}
        return retval

    def _db_get_attributes(self, attribute_prefix):
        for key, data in etcd.get_all('attribute/%s' % type(self).object_type,
                                      self.__uuid, prefix=attribute_prefix):
            yield key, data

    def _db_set_attribute(self, attribute, value):
        etcd.put('attribute/%s' % self.object_type, self.__uuid, attribute, value)

    def get_lock(self, subtype=None, ttl=60, timeout=db.ETCD_ATTEMPT_TIMEOUT,
                 relatedobjects=None, log_ctx=LOG, op=None):
        return db.get_lock(self.object_type, subtype, self.uuid, ttl=ttl,
                           timeout=timeout, relatedobjects=relatedobjects,
                           log_ctx=log_ctx, op=op)

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

            # Ensure state change is valid
            if new_value not in self.state_targets[orig.value]:
                raise exceptions.InvalidStateException(
                    'Invalid state change from %s to %s for object=%s uuid=%s',
                    orig.value, new_value, self.object_type, self.uuid)

            new_state = State(new_value, time.time())
            self._db_set_attribute('state', new_state)
            self.add_event('state changed',
                           '%s -> %s' % (orig.value, new_value))

    @property
    def error_msg(self):
        db_data = self._db_get_attribute('error_msg')
        if not db_data:
            return None
        return db_data

    @error_msg.setter
    def error_msg(self, msg):
        s = self.state
        if s.value != 'error':
            raise exceptions.InvalidStateException(
                'Object not in error state (state=%s, object=%s)' % (
                    s, self.object_type))
        self._db_set_attribute('error_msg', msg)

    @property
    def error_msg(self):
        db_data = self._db_get_attribute('error_msg')
        if not db_data:
            return None
        return db_data

    @error_msg.setter
    def error_msg(self, msg):
        s = self.state
        if s.value != 'error':
            raise exceptions.InvalidStateException(
                'Object not in error state (state=%s, object=%s)' % (
                    s, self.object_type))
        self._db_set_attribute('error_msg', msg)


def state_filter(states, o):
    return o.state.value in states


active_states_filter = partial(
    state_filter, ['initial', 'preflight', 'creating', 'created'])
inactive_states_filter = partial(state_filter, ['error', 'deleted'])


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
