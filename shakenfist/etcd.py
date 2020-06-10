import etcd3
import json
import logging

from shakenfist import config


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


class LockException(Exception):
    pass


class WriteException(Exception):
    pass


class ReadException(Exception):
    pass


def get_client():
    return etcd3.client(
        user=config.parsed.get('ETCD_USER'),
        password=config.parsed.get('ETCD_PASSWORD'),
        host=config.parsed.get('ETCD_SERVER'))


def get_lock(name, ttl=60):
    for attempt in range(3):
        try:
            return get_client().lock(name, ttl=ttl)
        except Exception as e:
            LOG.info('Failed to acquire lock, attempt %d: %s' % (attempt, e))

    raise LockException('Cannot acquire lock')


def _construct_key(objecttype, subtype, name):
    if subtype and name:
        return '/sf/%s/%s/%s' % (objecttype, subtype, name)
    if name:
        return '/sf/%s/%s' % (objecttype, name)
    if subtype:
        return '/sf/%s/%s/*' % (objecttype, subtype)
    return '/sf/%s' % objecttype


def put(objecttype, subtype, name, data, ttl=None):
    path = _construct_key(objecttype, subtype, name)
    encoded = json.dumps(data, indent=4, sort_keys=True)
    for attempt in range(3):
        try:
            return get_client().put(path, encoded, lease=None)
        except Exception as e:
            LOG.info('Failed to write %s, attempt %d: %s' % (path, attempt, e))

    raise WriteException('Cannot write %s' % path)


def get(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    for attempt in range(3):
        try:
            return json.loads(get_client().get(path))
        except Exception as e:
            LOG.info('Failed to read %s, attempt %d: %s' % (path, attempt, e))

    raise ReadException('Cannot read %s' % path)


def get_all(objecttype, subtype, sort_order=None):
    path = _construct_key(objecttype, subtype, None)
    for attempt in range(3):
        try:
            for value, meta in get_client().get_prefix(path, sort_order=sort_order):
                yield json.loads(value)
            return
        except Exception as e:
            LOG.info('Failed to fetch all %s, attempt %d: %s'
                     % (path, attempt, e))

    raise ReadException('Cannot fetch all %s' % path)


def delete(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    for attempt in range(3):
        try:
            get_client().delete(path)
            return
        except Exception as e:
            LOG.info('Failed to delete %s, attempt %d: %s' %
                     (path, attempt, e))

    raise WriteException('Cannot delete %s' % path)
