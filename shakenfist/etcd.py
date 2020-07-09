import etcd3
import json
import logging
from logging import handlers as logging_handlers
import time

####################################################################
# Please do not call this file directly, but instead call it via   #
# the db.py abstraction.                                           #
####################################################################


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


ETCD_ATTEMPTS = 5
ETCD_ATTEMPT_DELAY = 0.5


class LockException(Exception):
    pass


class WriteException(Exception):
    pass


class ReadException(Exception):
    pass


def get_lock(name, ttl=60):
    start = time.time()
    for attempt in range(ETCD_ATTEMPTS):
        try:
            return etcd3.client().lock(name, ttl=ttl)
        except Exception as e:
            LOG.info('Failed to acquire lock, attempt %d: %s' % (attempt, e))
            time.sleep(ETCD_ATTEMPT_DELAY)
        finally:
            LOG.debug('Locked etcd key "%s" after %.02f seconds'
                      % (name, time.time() - start))

    raise LockException('Cannot acquire lock')


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
