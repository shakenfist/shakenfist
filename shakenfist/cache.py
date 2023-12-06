from shakenfist_utilities import logs
import time

from shakenfist import etcd


LOG, _ = logs.setup(__name__)


def read_object_state_cache(object_type, state):
    c = etcd.get('cache', object_type, state)
    if not c:
        c = {}
    return c


def read_object_state_cache_many(object_type, states):
    # Prefilters need a consistent view across several states, so are the only
    # example of a read which holds a lock.
    out = []
    with etcd.get_lock('cache', None, object_type, op='Cache read many'):
        for key, data in etcd.get_prefix('/sf/cache/%s' % object_type):
            if type(data) is not dict:
                LOG.error('Ignoring malformed cache entry %s = %s' % (key, data))
                continue

            state = key.split('/')[-1]
            if state and state in states:
                uuids = list(data.keys())
                if uuids:
                    out.extend(uuids)
    return out


def update_object_state_cache(object_type, object_uuid, old_state, new_state):
    with etcd.get_lock('cache', None, object_type, op='Cache update'):
        # We have a special case list of objects in all states
        c = read_object_state_cache(object_type, '_all_')
        changed = False
        if new_state == 'hard-deleted' and object_uuid in c:
            del c[object_uuid]
            changed = True
        elif object_uuid not in c:
            c[object_uuid] = time.time()
            changed = True
        if changed:
            etcd.put('cache', object_type, '_all_', c)

        # And then the actual per-state cache
        c = read_object_state_cache(object_type, old_state)
        changed = False
        if object_uuid in c:
            del c[object_uuid]
            changed = True
        if changed:
            etcd.put('cache', object_type, old_state, c)

        c = read_object_state_cache(object_type, new_state)
        c[object_uuid] = time.time()
        etcd.put('cache', object_type, new_state, c)


def clobber_object_state_cache(object_type, state, object_uuids):
    with etcd.get_lock('cache', None, object_type, op='Cache clobber'):
        etcd.put('cache', object_type, state, object_uuids)
