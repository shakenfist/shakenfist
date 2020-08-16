import etcd3
import json
import logging
import time

####################################################################
# Please do not call this file directly, but instead call it via   #
# the db.py abstraction.                                           #
####################################################################


LOG = logging.getLogger(__name__)


ETCD_ATTEMPTS = 5
ETCD_ATTEMPT_DELAY = 0.5


class LockException(Exception):
    pass


class WriteException(Exception):
    pass


class ReadException(Exception):
    pass


class ActualLock(etcd3.Lock):
    def __init__(self, name, ttl=60, etcd_client=None, timeout=10):
        super(ActualLock, self).__init__(name, ttl, etcd_client)
        self.timeout = timeout

    def __enter__(self):
        LOG.debug('ActualLock.__enter__() timeout=%s name=%s',
                  self.timeout, self.name)
        if not self.acquire(timeout=self.timeout):
            raise LockException('Cannot acquire lock: %s' % self.name)
        return self


def get_lock(name, ttl=60, timeout=10):
    """Retrieves an Etcd lock object. It is not locked, to lock use acquire().

    The returned lock can be used as a context manager, with the lock being
    acquired on entry and released on exit. Note that the lock acquire process
    will have no timeout.
    """
    return ActualLock(name, ttl, etcd_client=etcd3.client(), timeout=timeout)


def _construct_key(objecttype, subtype, name):
    if subtype and name:
        return '/sf/%s/%s/%s' % (objecttype, subtype, name)
    if name:
        return '/sf/%s/%s' % (objecttype, name)
    if subtype:
        return '/sf/%s/%s/' % (objecttype, subtype)
    return '/sf/%s/' % objecttype


def put(objecttype, subtype, name, data, ttl=None):
    path = _construct_key(objecttype, subtype, name)
    encoded = json.dumps(data, indent=4, sort_keys=True)
    for attempt in range(ETCD_ATTEMPTS):
        try:
            return etcd3.client().put(path, encoded, lease=None)
        except Exception as e:
            LOG.info('Failed to write %s, attempt %d: %s' % (path, attempt, e))
            time.sleep(ETCD_ATTEMPT_DELAY)
        finally:
            LOG.debug('Wrote etcd key "%s"' % path)

    raise WriteException('Cannot write "%s"' % path)


def get(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    for attempt in range(ETCD_ATTEMPTS):
        try:
            value, _ = etcd3.client().get(path)
            if value is None:
                return None
            return json.loads(value)
        except Exception as e:
            LOG.info('Failed to read %s, attempt %d: %s' % (path, attempt, e))
            time.sleep(ETCD_ATTEMPT_DELAY)
        finally:
            LOG.debug('Read etcd key "%s"' % path)

    raise ReadException('Cannot read "%s"' % path)


def get_all(objecttype, subtype, sort_order=None):
    path = _construct_key(objecttype, subtype, None)
    for attempt in range(ETCD_ATTEMPTS):
        try:
            for value, _ in etcd3.client().get_prefix(path, sort_order=sort_order):
                yield json.loads(value)
            return
        except Exception as e:
            LOG.info('Failed to fetch all %s, attempt %d: %s'
                     % (path, attempt, e))
            time.sleep(ETCD_ATTEMPT_DELAY)
        finally:
            LOG.debug('Searched etcd range "%s"' % path)

    raise ReadException('Cannot fetch all "%s"' % path)


def delete(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    for attempt in range(ETCD_ATTEMPTS):
        try:
            etcd3.client().delete(path)
            return
        except Exception as e:
            LOG.info('Failed to delete %s, attempt %d: %s' %
                     (path, attempt, e))
            time.sleep(ETCD_ATTEMPT_DELAY)
        finally:
            LOG.debug('Deleted etcd key "%s"' % path)

    raise WriteException('Cannot delete "%s"' % path)


def delete_all(objecttype, subtype, sort_order=None):
    path = _construct_key(objecttype, subtype, None)
    for attempt in range(ETCD_ATTEMPTS):
        try:
            etcd3.client().delete_prefix(path)
            return
        except Exception as e:
            LOG.info('Failed to delete all %s, attempt %d: %s'
                     % (path, attempt, e))
            time.sleep(ETCD_ATTEMPT_DELAY)
        finally:
            LOG.debug('Deleted etcd range "%s"' % path)

    raise WriteException('Cannot delete all "%s"' % path)
