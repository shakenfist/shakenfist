import etcd3
import logging

from shakenfist import config


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


class LockException(Exception):
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
