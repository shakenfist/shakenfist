import time

from shakenfist_utilities import logs  # noreorder

from shakenfist import constants
from shakenfist import etcd
from shakenfist import exceptions


LOG, _ = logs.setup(__name__)
CACHE_VERSION = 4


def refresh_object_state_caches():
    timestamp = time.time()
    for object_type in constants.OBJECT_NAMES_TO_CLASSES:
        by_state = {
            'deleted': {}
        }

        for state in constants.get_object_class(object_type).state_targets:
            if state:
                by_state[state] = {}

        for key, _ in etcd.get_prefix_raw(f'/sf/{object_type}'):
            obj_uuid = key.split('/')[-1]
            obj = constants.get_object_class(object_type).from_db(
                obj_uuid, suppress_failure_audit=True)
            if obj:
                if obj.state.value not in by_state:
                    LOG.with_fields({
                        'state': obj.state.value,
                        'object_type': object_type,
                        'object_uuid': obj_uuid
                    }).error('Unknown state!')
                    continue

                by_state[obj.state.value][obj.uuid] = time.time()

        # NOTE(mikal): I am a bit concerned about the maximum transaction
        # size here, so we do a transaction per object type per state. We don't
        # hold a lock because the readers never take it so what's the point?
        previous_states = []
        for key, _ in etcd.get_prefix_raw(f'/sf/cache/{object_type}'):
            state = key.split('/')[-1]
            if state not in previous_states:
                previous_states.append(state)

        for state in by_state:
            if state in previous_states:
                previous_states.remove(state)

            etcd.delete_prefix(f'/sf/cache/{object_type}/{state}')
            mutations = []

            for objuuid in by_state[state]:
                path = f'/sf/cache/{object_type}/{state}/{objuuid}'
                mutations.append({
                    'path': path,
                    'original_data': None,
                    'new_data': {
                        'timestamp': timestamp
                    }
                })

            LOG.info(f'Cache update for {object_type} {state} has '
                     f'{len(mutations)} mutations')
            etcd.replace_many_raw(mutations)

        for state in previous_states:
            etcd.delete_prefix(f'/sf/cache/{object_type}/{state}')

    etcd.put_raw('/sf/cache/_version', {'version': CACHE_VERSION})


# Object state caches live in etcd under
#     /sf/cache/...object_type.../...state.../...uuid...
def read_object_state_cache(object_type, state):
    out = []
    for key, _ in etcd.get_prefix_raw(f'/sf/cache/{object_type}/{state}'):
        out.append(key.split('/')[-1])
    return out


def read_object_state_cache_many(object_type, states):
    # NOTE(mikal): this code relies on the fact that etc3gw implements get_prefix
    # as an etcd API range request, which is atomic. It therefore does not need
    # a lock to receive a consistent view of the cache, so long as everything
    # can be fetched in a single etcd API request.
    out = []
    for key, _ in etcd.get_prefix_raw(f'/sf/cache/{object_type}'):
        state = key.split('/')[-2]
        if state and state in states:
            out.append(key.split('/')[-1])
    return out


def read_object_state_cache_all(object_type):
    # The same as above, but return all states not a filtered view.
    out = []
    for key, _ in etcd.get_prefix_raw(f'/sf/cache/{object_type}'):
        out.append(key.split('/')[-1])
    return out


def _update_object_state_cache_attempt(
        object_type, object_uuid, old_state, new_state):
    mutations = []

    # And then the actual per-state cache
    if old_state:
        path = f'/sf/cache/{object_type}/{old_state}/{object_uuid}'
        mutations.append({
            'path': path,
            'original_data': etcd.get_raw(path),
            'new_data': None
        })

    path = f'/sf/cache/{object_type}/{new_state}/{object_uuid}'
    mutations.append({
        'path': path,
        'original_data': None,
        'new_data': {
            'timestamp': time.time()
        }
    })

    return etcd.replace_many_raw(mutations)


def update_object_state_cache(object_type, object_uuid, old_state, new_state):
    attempts = 0
    while attempts < 3:
        success, failures = _update_object_state_cache_attempt(
            object_type, object_uuid, old_state, new_state)
        if success:
            return

        # Is it possible someone else did this update for us?
        if (
            not etcd.get_raw(
                f'/sf/cache/{object_type}/{old_state}/{object_uuid}')
            and etcd.get_raw(f'/sf/cache/{object_type}/{new_state}/{object_uuid}')
        ):
            return

        time.sleep(0.2)
        attempts += 1

    failure_strings = []
    for f in failures:
        failure_strings.append(
            f'    {f["path"]}:\n'
            f'        actual: {f["actual"]}\n'
            f'        desired: {f["desired"]}\n'
            f'        replacement: {f["replacement"]}')
    failure_dump = '\n'.join(failure_strings)
    raise exceptions.LocklessUpdateFailed(
        f'Lockless object state cache update for {object_type} {object_uuid} '
        f'from {old_state} to {new_state} failed. Failures:\n{failure_dump}')


# Blob hash caches live in etcd under /sf/blob_by_hash/...algorithm.../...hash...
def update_blob_hash_cache(blob_uuid, hashes):
    for alg in hashes:
        with etcd.ClusterLock(
                'blob_by_hash', alg, hashes[alg], op='Blob hash cache update'):
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
