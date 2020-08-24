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
        LOG.debug('Lock attempt: timeout=%s name=%s',
                  self.timeout, self.name)
        for attempt in range(ETCD_ATTEMPTS):
            try:
                if not self.acquire(timeout=self.timeout):
                    raise LockException('Cannot acquire lock: %s' % self.name)
                return self

            except etcd3.exceptions.ConnectionFailedError:
                time.sleep(ETCD_ATTEMPTS)

        raise LockException('Could not acquire lock after retries.')


def get_lock(objecttype, subtype, name, ttl=60, timeout=10):
    """Retrieves an Etcd lock object. It is not locked, to lock use acquire().

    The returned lock can be used as a context manager, with the lock being
    acquired on entry and released on exit. Note that the lock acquire process
    will have no timeout.
    """
    path = _construct_key(objecttype, subtype, name)
    return ActualLock(path, ttl, etcd_client=etcd3.client(), timeout=timeout)


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


def enqueue(queuename, workitem):
    for attempt in range(ETCD_ATTEMPTS):
        try:
            with get_lock('queue', None, queuename) as _:
                i = 0
                entry_time = time.time()
                jobname = '%s-%03d' % (entry_time, i)

                while get('queue', queuename, jobname):
                    i += 1
                    jobname = '%s-%03d' % (entry_time, i)

                put('queue', queuename, jobname, workitem)
                LOG.info('Enqueued workitem %s for queue %s with work %s'
                         % (jobname, queuename, workitem))
                return

        except Exception as e:
            LOG.info('Failed to enqueue for %s, attempt %d: %s'
                     % (queuename, attempt, e))
            time.sleep(ETCD_ATTEMPT_DELAY)


def dequeue(queuename):
    for attempt in range(ETCD_ATTEMPTS):
        try:
            with get_lock('queue', None, queuename) as _:
                queue_path = _construct_key('queue', queuename, None)

                client = etcd3.client()
                for data, metadata in client.get_prefix(queue_path, sort_order='ascend'):
                    jobname = str(metadata.key).split('/')[-1]
                    workitem = json.loads(data)

                    put('processing', queuename, jobname, workitem)
                    client.delete(metadata.key)
                    LOG.info('Moved workitem %s from queue to processing for %s with work %s'
                             % (jobname, queuename, workitem))

                    return jobname, workitem

                return None, None

        except Exception as e:
            LOG.info('Failed to dequeue for %s, attempt %d: %s'
                     % (queuename, attempt, e))
            time.sleep(ETCD_ATTEMPT_DELAY)


def resolve(queuename, jobname):
    for attempt in range(ETCD_ATTEMPTS):
        try:
            delete('processing', queuename, jobname)
            LOG.info('Resolved workitem %s for queue %s'
                     % (jobname, queuename))
            return

        except Exception as e:
            LOG.info('Failed to resolve workitem %s for %s, attempt %d: %s'
                     % (jobname, queuename, attempt, e))
            time.sleep(ETCD_ATTEMPT_DELAY)
