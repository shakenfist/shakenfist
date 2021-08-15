import json
import os
import psutil
import time

from etcd3gw.client import Etcd3Client
from etcd3gw.exceptions import InternalServerError
from etcd3gw.lock import Lock

from shakenfist.config import config
from shakenfist import db
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist import util
from shakenfist.tasks import QueueTask
from shakenfist import baseobject


####################################################################
# Please do not call this file directly, but instead call it via   #
# the db.py abstraction.                                           #
####################################################################


LOG, _ = logutil.setup(__name__)

LOCK_PREFIX = '/sflocks'


class WrappedEtcdClient(Etcd3Client):
    def __init__(self, host='localhost', port=2379, protocol='http',
                 ca_cert=None, cert_key=None, cert_cert=None, timeout=None,
                 api_path='/v3beta/'):
        # Work around https://opendev.org/openstack/etcd3gw/commit/7a1a2b5a672605ae549c73ed18302b7abd9e0e30
        # making things not work for us.
        if api_path == '/v3alpha':
            raise Exception('etcd3 v3alpha endpoint is known not to work')

        return super(WrappedEtcdClient, self).__init__(
            host=host, port=port, protocol=protocol, ca_cert=ca_cert,
            cert_key=cert_key, cert_cert=cert_cert, timeout=timeout,
            api_path=api_path)


def retry_etcd_forever(func):
    """Retry the Etcd server forever.

    If the DB is unable to process the request then SF cannot operate,
    therefore wait until it comes back online. If the DB falls out of sync with
    the system then we will have bigger problems than a small delay.

    If the etcd server is not running, then a ConnectionFailedError exception
    will occur. This is deliberately allowed to cause an SF daemon failure to
    bring attention to the deeper problem.
    """
    def wrapper(*args, **kwargs):
        count = 0
        while True:
            try:
                return func(*args, **kwargs)
            except InternalServerError as e:
                LOG.error('Etcd3gw Internal Server Error: %s' % e)
            time.sleep(count/10.0)
            count += 1
    return wrapper


class ActualLock(Lock):
    def __init__(self, objecttype, subtype, name, ttl=120,
                 client=None, timeout=1000000000, log_ctx=LOG,
                 op=None):

        self.path = _construct_key(objecttype, subtype, name)
        super(ActualLock, self).__init__(self.path, ttl=ttl, client=client)

        self.objecttype = objecttype
        self.objectname = name
        self.timeout = min(timeout, 1000000000)
        self.log_ctx = log_ctx.with_field('path', self.path)
        self.operation = op

        # We override the UUID of the lock with something more helpful to debugging
        self._uuid = json.dumps(
            {
                'node': config.NODE_NAME,
                'pid': os.getpid(),
                'operation': self.operation
            },
            indent=4, sort_keys=True)

        # We also override the location of the lock so that we're in our own spot
        self.key = LOCK_PREFIX + self.path

    @retry_etcd_forever
    def get_holder(self):
        value = WrappedEtcdClient().get(self.key, metadata=True)
        if value is None or len(value) == 0:
            return None, NotImplementedError

        if not value[0][0]:
            return None, None

        d = json.loads(value[0][0])
        return d['node'], d['pid']

    def __enter__(self):
        start_time = time.time()
        slow_warned = False
        threshold = int(config.SLOW_LOCK_THRESHOLD)

        while time.time() - start_time < self.timeout:
            res = self.acquire()
            if res:
                duration = time.time() - start_time
                if duration > threshold:
                    db.add_event(self.objecttype, self.objectname,
                                 'lock', 'acquired', None,
                                 'Waited %d seconds for lock' % duration)
                    self.log_ctx.with_field('duration', duration
                                            ).info('Acquiring a lock was slow')
                return self

            duration = time.time() - start_time
            if (duration > threshold and not slow_warned):
                db.add_event(self.objecttype, self.objectname,
                             'lock', 'acquire', None,
                             'Waiting for lock more than threshold')

                node, pid = self.get_holder()
                self.log_ctx.with_fields({'duration': duration,
                                          'threshold': threshold,
                                          'holder-pid': pid,
                                          'holder-node': node,
                                          'requesting-op': self.operation,
                                          }).info('Waiting for lock')
                slow_warned = True

            time.sleep(1)

        duration = time.time() - start_time
        db.add_event(self.objecttype, self.objectname,
                     'lock', 'failed', None,
                     'Failed to acquire lock after %.02f seconds' % duration)

        node, pid = self.get_holder()
        self.log_ctx.with_fields({'duration': duration,
                                  'holder-pid': pid,
                                  'holder-node': node,
                                  'requesting-op': self.operation,
                                  }).info('Failed to acquire lock')

        raise exceptions.LockException(
            'Cannot acquire lock %s, timed out after %.02f seconds'
            % (self.name, duration))

    def __exit__(self, _exception_type, _exception_value, _traceback):
        if not self.release():
            locks = list(get_all(LOCK_PREFIX, None))
            self.log_ctx.withFields({'locks': locks,
                                     'key': self.name,
                                     }).error('Cannot release lock')
            raise exceptions.LockException(
                'Cannot release lock: %s' % self.name)


def get_lock(objecttype, subtype, name, ttl=60, timeout=10, log_ctx=LOG,
             op=None):
    """Retrieves an etcd lock object. It is not locked, to lock use acquire().

    The returned lock can be used as a context manager, with the lock being
    acquired on entry and released on exit. Note that the lock acquire process
    will have no timeout.
    """
    return ActualLock(objecttype, subtype, name, ttl=ttl, client=WrappedEtcdClient(),
                      log_ctx=log_ctx, timeout=timeout, op=op)


def refresh_lock(lock, log_ctx=LOG):
    if not lock.is_acquired():
        log_ctx.with_field('lock', lock.name).info(
            'Attempt to refresh an expired lock')
        raise exceptions.LockException(
            'The lock on %s has expired.' % lock.path)

    lock.refresh()
    log_ctx.with_field('lock', lock.name).debug('Refreshed lock')


@retry_etcd_forever
def clear_stale_locks():
    # Remove all locks held by former processes on this node. This is required
    # after an unclean restart, otherwise we need to wait for these locks to
    # timeout and that can take a long time.
    client = WrappedEtcdClient()

    for data, metadata in client.get_prefix(
            LOCK_PREFIX + '/', sort_order='ascend', sort_target='key'):
        lockname = str(metadata['key']).replace(LOCK_PREFIX + '/', '')
        holder = json.loads(data)
        node = holder['node']
        pid = int(holder['pid'])

        if node == config.NODE_NAME and not psutil.pid_exists(pid):
            client.delete(metadata['key'])
            LOG.with_fields({'lock': lockname,
                             'old-pid': pid,
                             'old-node': node,
                             }).warning('Removed stale lock')


@retry_etcd_forever
def get_existing_locks():
    key_val = {}
    for value in WrappedEtcdClient().get_prefix(LOCK_PREFIX + '/'):
        key_val[value[1]['key'].decode('utf-8')] = json.loads(value[0])
    return key_val


def _construct_key(objecttype, subtype, name):
    if subtype and name:
        return '/sf/%s/%s/%s' % (objecttype, subtype, name)
    if name:
        return '/sf/%s/%s' % (objecttype, name)
    if subtype:
        return '/sf/%s/%s/' % (objecttype, subtype)
    return '/sf/%s/' % objecttype


class JSONEncoderTasks(json.JSONEncoder):
    def default(self, obj):
        if QueueTask.__subclasscheck__(type(obj)):
            return obj.obj_dict()
        if type(obj) is baseobject.State:
            return obj.obj_dict()
        return json.JSONEncoder.default(self, obj)


@retry_etcd_forever
def put(objecttype, subtype, name, data, ttl=None):
    path = _construct_key(objecttype, subtype, name)
    encoded = json.dumps(data, indent=4, sort_keys=True, cls=JSONEncoderTasks)
    WrappedEtcdClient().put(path, encoded, lease=None)


@retry_etcd_forever
def create(objecttype, subtype, name, data, ttle=None):
    path = _construct_key(objecttype, subtype, name)
    encoded = json.dumps(data, indent=4, sort_keys=True, cls=JSONEncoderTasks)
    return WrappedEtcdClient().create(path, encoded, lease=None)


@retry_etcd_forever
def get(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    value = WrappedEtcdClient().get(path, metadata=True)
    if value is None or len(value) == 0:
        return None
    return json.loads(value[0][0])


@retry_etcd_forever
def get_all(objecttype, subtype, prefix=None, sort_order=None):
    path = _construct_key(objecttype, subtype, prefix)
    for data, metadata in WrappedEtcdClient().get_prefix(path, sort_order=sort_order, sort_target='key'):
        yield str(metadata['key'].decode('utf-8')), json.loads(data)


@retry_etcd_forever
def get_all_dict(objecttype, subtype=None, sort_order=None):
    path = _construct_key(objecttype, subtype, None)
    key_val = {}
    for value in WrappedEtcdClient().get_prefix(path, sort_order=sort_order, sort_target='key'):
        key_val[value[1]['key'].decode('utf-8')] = json.loads(value[0])
    return key_val


@retry_etcd_forever
def delete(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    WrappedEtcdClient().delete(path)


@retry_etcd_forever
def delete_all(objecttype, subtype):
    path = _construct_key(objecttype, subtype, None)
    WrappedEtcdClient().delete_prefix(path)


def enqueue(queuename, workitem):
    with get_lock('queue', None, queuename, op='Enqueue'):
        i = 0
        entry_time = time.time()
        jobname = '%s-%03d' % (entry_time, i)

        while get('queue', queuename, jobname):
            i += 1
            jobname = '%s-%03d' % (entry_time, i)

        put('queue', queuename, jobname, workitem)
        LOG.with_fields({'jobname': jobname,
                         'queuename': queuename,
                         'workitem': workitem,
                         }).info('Enqueued workitem')


def _all_subclasses(cls):
    all = cls.__subclasses__()
    for sc in cls.__subclasses__():
        all += _all_subclasses(sc)
    return all


def _find_class(task_item):
    if not isinstance(task_item, dict):
        return task_item

    item = task_item
    for task_class in _all_subclasses(QueueTask):
        if task_class.name() and task_item.get('task') == task_class.name():
            del task_item['task']
            # This is where new QueueTask subclass versions should be handled
            del task_item['version']
            item = task_class(**task_item)
            break

    return item


def decodeTasks(obj):
    if not isinstance(obj, dict):
        return obj

    if 'tasks' in obj:
        task_list = []
        for task_item in obj['tasks']:
            task_list.append(_find_class(task_item))
        return {'tasks': task_list}

    if 'task' in obj:
        return _find_class(obj)

    return obj


@retry_etcd_forever
def dequeue(queuename):
    queue_path = _construct_key('queue', queuename, None)
    client = WrappedEtcdClient()

    # We only hold the lock if there is anything in the queue
    if not client.get_prefix(queue_path):
        return None, None

    with get_lock('queue', None, queuename, op='Dequeue'):
        for data, metadata in client.get_prefix(queue_path, sort_order='ascend', sort_target='key'):
            jobname = str(metadata['key']).split('/')[-1].rstrip("'")
            workitem = json.loads(data, object_hook=decodeTasks)
            put('processing', queuename, jobname, workitem)
            client.delete(metadata['key'])
            LOG.with_fields({'jobname': jobname,
                             'queuename': queuename,
                             'workitem': workitem,
                             }).info('Moved workitem from queue to processing')

            return jobname, workitem

    return None, None


def resolve(queuename, jobname):
    with get_lock('queue', None, queuename, op='Resolve'):
        delete('processing', queuename, jobname)
        LOG.with_fields({'jobname': jobname,
                         'queuename': queuename,
                         }).info('Resolved workitem')


def get_queue_length(queuename):
    queued = len(list(get_all('queue', queuename)))
    processing = len(list(get_all('processing', queuename)))
    return processing, queued


@retry_etcd_forever
def _restart_queue(queuename):
    queue_path = _construct_key('processing', queuename, None)
    with get_lock('queue', None, queuename, op='Restart'):
        for data, metadata in WrappedEtcdClient().get_prefix(queue_path, sort_order='ascend'):
            jobname = str(metadata['key']).split('/')[-1].rstrip("'")
            workitem = json.loads(data)
            put('queue', queuename, jobname, workitem)
            delete('processing', queuename, jobname)
            LOG.with_fields({'jobname': jobname,
                             'queuename': queuename,
                             }).warning('Reset workitem')


def restart_queues():
    # Move things which were in processing back to the queue because
    # we didn't complete them before crashing.
    if util.is_network_node():
        _restart_queue('networknode')
    _restart_queue(config.NODE_NAME)
