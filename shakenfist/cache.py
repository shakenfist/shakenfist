import time

from shakenfist import etcd


def read_object_state_cache(object_type, state):
    c = etcd.get('cache', object_type, state)
    if not c:
        c = {}
    return c


def update_object_state_cache(object_type, object_uuid, old_state, new_state):
    with etcd.get_lock('cache', None, object_type, op='Cache update'):
        c = read_object_state_cache(object_type, old_state)
        if object_uuid in c:
            del c[object_uuid]
        etcd.put('cache', object_type, old_state, c)

        c = read_object_state_cache(object_type, new_state)
        c[object_uuid] = time.time()
        etcd.put('cache', object_type, new_state, c)
