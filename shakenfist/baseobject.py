
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

    def __init__(self, object_uuid, version=None):
        self.__uuid = object_uuid
        self.__version = version

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

    def get_lock_attr(self, name, op):
        return db.get_lock('attribute/%s' % self.object_type,
                           self.__uuid, name, op=op)

    # Properties common to all objects which are routed to attributes
    @property
    def state(self):
        return self._db_get_attribute('state')

    def update_state(self, state, error_message=None):
        with self.get_lock_attr('state', 'State update'):
            dbstate = self.state
            orig_state = dbstate.get('state')
            dbstate['state'] = state
            dbstate['state_updated'] = time.time()

            if error_message:
                dbstate['error_message'] = error_message

            self._db_set_attribute('state', dbstate)
            self.add_event('state changed', '%s -> %s' % (orig_state, state))


def state_filter(states, o):
    return o.state.get('state') in states


active_states_filter = partial(
    state_filter, ['initial', 'preflight', 'creating', 'created'])
inactive_states_filter = partial(state_filter, ['error', 'deleted'])


def state_age_filter(delay, o):
    now = time.time()
    return (now - o.state.get('state_updated', now)) > delay


def namespace_filter(namespace, o):
    if namespace == 'system':
        return True
    return o.namespace == namespace
