import time

from shakenfist_utilities import logs  # noreorder

from shakenfist import constants
from shakenfist import etcd
from shakenfist import exceptions


LOG, _ = logs.setup(__name__)
CACHE_VERSION = 4


def _check_cache_version():
    version = etcd.get_raw('/sf/cache/_version')
    if not version:
        version = {}
    current = version.get('version', 0) == CACHE_VERSION
    if not current:
        LOG.warning('Cache version is not current version, rebuilding')
        refresh_object_state_caches()
    return current


def refresh_object_state_caches():
    for object_type in constants.OBJECT_NAMES_TO_CLASSES:
        with etcd.get_lock('cache', None, object_type, op='Cache refresh'):
            by_state = {
                'deleted': {}
            }

            for state in constants.get_object_class(object_type).state_targets:
                if state:
                    by_state[state] = {}

            for key, _ in etcd.get_prefix(f'/sf/{object_type}'):
                obj_uuid = key.split('/')[-1]
                obj = constants.get_object_class(object_type).from_db(
                    obj_uuid)
                if obj:
                    if obj.state.value not in by_state:
                        LOG.with_fields({
                            'state': obj.state.value,
                            'object_type': object_type,
                            'object_uuid': obj_uuid
                        }).error('Unknown state!')
                        continue

                    by_state[obj.state.value][obj.uuid] = time.time()

            for state in by_state:
                clobber_object_state_cache(
                    object_type, state, by_state[state])

            # Remove the obsolete _all_ meta state
            etcd.delete_all('cache', object_type, '_all_')

    etcd.put_raw('/sf/cache/_version', {'version': CACHE_VERSION})


# Object state caches live in etcd under
#     /sf/cache/...object_type.../...state.../...uuid...
def read_object_state_cache(object_type, state):
    if not _check_cache_version():
        return None

    out = []
    for key, _ in etcd.get_prefix(f'/sf/cache/{object_type}/{state}'):
        out.append(key.split('/')[-1])
    return out


def read_object_state_cache_many(object_type, states):
    # NOTE(mikal): this code relies on the fact that etc3gw implements get_prefix
    # as an etcd API range request, which is atomic. It therefore does not need
    # a lock to receive a consistent view of the cache, so long as everything
    # can be fetched in a single etcd API request.
    if not _check_cache_version():
        return []

    out = []
    for key, _ in etcd.get_prefix(f'/sf/cache/{object_type}'):
        state = key.split('/')[-2]
        if state and state in states:
            out.append(key.split('/')[-1])
    return out


def read_object_state_cache_all(object_type):
    # The same as above, but return all states not a filtered view.
    if not _check_cache_version():
        return []

    out = []
    for key, _ in etcd.get_prefix(f'/sf/cache/{object_type}'):
        out.append(key.split('/')[-1])
    return out


def _update_object_state_cache_attempt(object_type, object_uuid, old_state, new_state):
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
    if not _check_cache_version():
        return None

    attempts = 0
    failures = []
    while attempts < 3:
        success, failures = _update_object_state_cache_attempt(
            object_type, object_uuid, old_state, new_state)
        if success:
            return

        time.sleep(0.2)
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
    # Caller is assumed to be holding a lock. This does not check version, as it
    # is how we update versions.
    # NOTE(mikal): we _should_ be able to do this in a single transaction once
    # we've gotten rid of etcd3gw entirely.
    etcd.delete_prefix(f'/sf/cache/{object_type}/{state}')

    timestamp = time.time()
    mutations = []
    for objuuid in object_uuids:
        path = f'/sf/cache/{object_type}/{state}/{objuuid}'
        mutations.append({
            'path': path,
            'original_data': None,
            'new_data': {
                'timestamp': timestamp
            }
        })
    return etcd.replace_many_raw(mutations)


# Blob hash caches live in etcd under /sf/blob_by_hash/...algorithm.../...hash...
def update_blob_hash_cache(blob_uuid, hashes):
    if not _check_cache_version():
        return None

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
    if not _check_cache_version():
        return None

    c = etcd.get('blob_by_hash', alg, hash)
    if not c:
        return []
    if 'blobs' not in c:
        return []
    return c['blobs']
