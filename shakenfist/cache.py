import copy
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist import exceptions


LOG, _ = logs.setup(__name__)


# Object state caches live in etcd under /sf/cache/...objectype.../...state...
def read_object_state_cache(object_type, state):
    c = etcd.get('cache', object_type, state)
    if not c:
        c = {}
    return c


def read_object_state_cache_many(object_type, states):
    # NOTE(mikal): this code relies on the fact that etc3gw implements get_prefix
    # as an etcd API range request, which is atomic. It therefore does not need
    # a lock to receive a consistent view of the cache, so long as everything
    # can be fetched in a single etcd API request.
    out = []
    for key, data in etcd.get_prefix('/sf/cache/%s' % object_type):
        if type(data) is not dict:
            LOG.error(f'Ignoring malformed cache entry {key} = {data}')
            continue

        state = key.split('/')[-1]
        if state and state in states:
            uuids = list(data.keys())
            if uuids:
                out.extend(uuids)
    return out


def _update_object_state_cache_attempt(object_type, object_uuid, old_state, new_state):
    mutations = []

    # We have a special case list of objects in all states
    original = etcd.get('cache', object_type, '_all_')
    if not original:
        updated = {}
    else:
        updated = copy.copy(original)
    changed = False

    if new_state == 'hard-deleted' and object_uuid in updated:
        del updated[object_uuid]
        changed = True
    elif object_uuid not in updated:
        updated[object_uuid] = time.time()
        changed = True
    if changed:
        mutations.append({
            'path': etcd._construct_key('cache', object_type, '_all_'),
            'original_data': original,
            'new_data': updated
        })

    # And then the actual per-state cache
    if old_state:
        original = etcd.get('cache', object_type, old_state)
        if not original:
            updated = {}
        else:
            updated = copy.copy(original)

        if object_uuid in updated:
            del updated[object_uuid]
            mutations.append({
                'path': etcd._construct_key('cache', object_type, old_state),
                'original_data': original,
                'new_data': updated
            })

    original = etcd.get('cache', object_type, new_state)
    if not original:
        updated = {}
    else:
        updated = copy.copy(original)

    updated[object_uuid] = time.time()
    mutations.append({
        'path': etcd._construct_key('cache', object_type, new_state),
        'original_data': original,
        'new_data': updated
    })

    return etcd.replace_many_raw(mutations)


def update_object_state_cache(object_type, object_uuid, old_state, new_state):
    attempts = 0
    failures = []
    while attempts < 3:
        success, failures = _update_object_state_cache_attempt(
            object_type, object_uuid, old_state, new_state)
        if success:
            return
        attempts += 1

    failure_strings = []
    for f in failures:
        failure_strings.append(
            f'    {f["path"]}:\n'
            f'        actual: {f["actual"]}\n'
            f'        desired: {f["desired"]}')
    failure_dump = '\n'.join(failure_strings)
    raise exceptions.LocklessUpdateFailed(
        f'Lockless object state cache update for {object_type} {object_uuid} '
        f'from {old_state} to {new_state} failed. Failures:\n{failure_dump}')


def clobber_object_state_cache(object_type, state, object_uuids):
    # Caller is assumed to be holding a lock
    etcd.put('cache', object_type, state, object_uuids)


# Blob hash caches live in etcd under /sf/blob_by_hash/...algorithm.../...hash...
def update_blob_hash_cache(blob_uuid, hashes):
    for alg in hashes:
        with etcd.get_lock('blob_by_hash', alg, hashes[alg],
                           op='Blob hash cache update'):
            c = etcd.get('blob_by_hash', alg, hashes[alg])
            if not c:
                c = {}
            if 'blobs' not in c:
                c['blobs'] = []
            if blob_uuid not in c['blobs']:
                c['blobs'].append(blob_uuid)
                etcd.put('blob_by_hash', alg, hashes[alg], c)


def search_blob_hash_cache(alg, hash):
    c = etcd.get('blob_by_hash', alg, hash)
    if not c:
        return []
    if 'blobs' not in c:
        return []
    return c['blobs']
