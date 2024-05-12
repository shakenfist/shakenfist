from collections import defaultdict
from etcd3gw.client import Etcd3Client
from etcd3gw.exceptions import InternalServerError
from etcd3gw.lock import Lock
from etcd3gw.utils import _encode, _increment_last_byte
import json
import os
import psutil
import requests
from shakenfist_utilities import (logs, random as util_random)
import threading
import time

from shakenfist import baseobject
from shakenfist.config import config
from shakenfist import exceptions
from shakenfist.tasks import QueueTask, FetchBlobTask
from shakenfist.util import callstack as util_callstack


LOG, _ = logs.setup(__name__)
LOCK_PREFIX = '/sflocks'


class WrappedEtcdClient(Etcd3Client):
    def __init__(self, host=None, port=2379, protocol='http',
                 ca_cert=None, cert_key=None, cert_cert=None, timeout=None,
                 api_path='/v3beta/'):
        if not host:
            host = config.ETCD_HOST

        # Work around https://opendev.org/openstack/etcd3gw/commit/7a1a2b5a672605ae549c73ed18302b7abd9e0e30
        # making things not work for us.
        if api_path == '/v3alpha':
            raise Exception('etcd3 v3alpha endpoint is known not to work')

        # Cache config options so we can reuse them when we rebuild connections.
        self.ca_cert = ca_cert
        self.cert_key = cert_key
        self.cert_cert = cert_cert
        self.timeout = timeout

        if config.LOG_ETCD_CONNECTIONS:
            LOG.info('Building new etcd connection')
        return super().__init__(
            host=host, port=port, protocol=protocol, ca_cert=ca_cert,
            cert_key=cert_key, cert_cert=cert_cert, timeout=timeout,
            api_path=api_path)

    # Replace the upstream implementation with one which allows for limits on range
    # queries instead of just erroring out for big result sets.
    def get_prefix(self, key_prefix, sort_order=None, sort_target=None, limit=0):
        """Get a range of keys with a prefix.

        :param sort_order: 'ascend' or 'descend' or None
        :param key_prefix: first key in range

        :returns: sequence of (value, metadata) tuples
        """
        return self.get(key_prefix,
                        metadata=True,
                        range_end=_encode(_increment_last_byte(key_prefix)),
                        sort_order=sort_order,
                        sort_target=sort_target,
                        limit=limit)

    # Wrap post() to retry on errors. These errors are caused by our long lived
    # connections sometimes being dropped.
    def post(self, *args, **kwargs):
        try:
            return super().post(*args, **kwargs)
        except Exception as e:
            LOG.info('Retrying after receiving etcd error: %s' % e)

            self.session = requests.Session()
            if self.timeout is not None:
                self.session.timeout = self.timeout
            if self.ca_cert is not None:
                self.session.verify = self.ca_cert
            if self.cert_cert is not None and self.cert_key is not None:
                self.session.cert = (self.cert_cert, self.cert_key)
            return super().post(*args, **kwargs)


# This module stores some state in thread local storage.
local = threading.local()
local.sf_etcd_client = None


def get_etcd_client():
    c = getattr(local, 'sf_etcd_client', None)
    if not c:
        c = local.sf_etcd_client = WrappedEtcdClient()
    return c


def reset_client():
    local.sf_etcd_client = None


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
                 client=None, timeout=120, log_ctx=LOG,
                 op=None):
        self.path = _construct_key(objecttype, subtype, name)
        super().__init__(self.path, ttl=ttl, client=client)

        self.objecttype = objecttype
        self.objectname = name
        self.timeout = timeout
        self.operation = op
        self.lockid = util_random.random_id()

        node = config.NODE_NAME
        pid = os.getpid()
        caller = util_callstack.get_caller(offset=3)

        self.log_ctx = log_ctx.with_fields(
            {
                'lock': self.path,
                'node': node,
                'pid': pid,
                'line': caller,
                'operation': self.operation,
                'id': self.lockid
            })

        # We override the UUID of the lock with something more helpful to debugging
        self._uuid = json.dumps(
            {
                'node': node,
                'pid': pid,
                'line': caller,
                'operation': self.operation,
                'id': self.lockid
            },
            indent=4, sort_keys=True)

        # We also override the location of the lock so that we're in our own spot
        self.key = LOCK_PREFIX + self.path

    @retry_etcd_forever
    def get_holder(self, key_prefix=''):
        value = get_etcd_client().get(self.key)
        if value is None or len(value) == 0:
            return {'holder': None}

        if not value[0][0]:
            return {'holder': None}

        holder = json.loads(value[0])
        if key_prefix:
            new_holder = {}
            for key in holder:
                new_holder[f'{key_prefix}-{key}'] = holder[key]
            return new_holder

        return holder

    def refresh(self):
        super().refresh()
        self.log_ctx.info('Refreshed lock')

    def __enter__(self):
        start_time = time.time()
        slow_warned = False
        threshold = self.timeout / 2

        while time.time() - start_time < self.timeout:
            res = self.acquire()
            duration = time.time() - start_time
            if res:
                current = self.get_holder()
                current_id = current.get('id')
                if current_id != self.lockid:
                    self.log_ctx.with_fields({
                        'current_id': current_id,
                        'duration': duration
                        }).error('We should hold lock, but do not!')
                elif duration > threshold:
                    self.log_ctx.with_fields({
                        'duration': duration}).info('Acquired lock, but it was slow')
                    return self
                else:
                    self.log_ctx.info('Acquired lock')
                    return self

            if (duration > threshold and not slow_warned):
                current = self.get_holder(key_prefix='current')
                self.log_ctx.with_fields(current).with_fields({
                    'duration': duration,
                    'threshold': threshold
                    }).info('Waiting to acquire lock')
                slow_warned = True

            time.sleep(1)

        current = self.get_holder(key_prefix='current')
        self.log_ctx.with_fields(current).with_fields({
            'duration': time.time() - start_time
            }).info('Failed to acquire lock')

        raise exceptions.LockException(
            'Cannot acquire lock %s, timed out after %.02f seconds'
            % (self.name, self.timeout))

    def __exit__(self, _exception_type, _exception_value, _traceback):
        attempts = 0
        while attempts < 4:
            if self.release():
                self.log_ctx.info('Released lock')
                return
            else:
                attempts += 1
                locks = list(get_all(LOCK_PREFIX, None))
                self.log_ctx.with_fields({
                    'locks': locks,
                    'key': self.name,
                    'attempt': attempts,
                    }).error('Failed to release lock')
            time.sleep(0.5)

        raise exceptions.LockException('Cannot release lock: %s' % self.name)

    def __str__(self):
        return ('ActualLock(%s %s, op %s, with timeout %s)'
                % (self.objecttype, self.objectname, self.timeout, self.operation))


def get_lock(objecttype, subtype, name, ttl=60, timeout=10, log_ctx=LOG,
             op=None):
    """Retrieves an etcd lock object. It is not locked, to lock use acquire().

    The returned lock can be used as a context manager, with the lock being
    acquired on entry and released on exit. Note that the lock acquire process
    will have no timeout.
    """
    return ActualLock(objecttype, subtype, name, ttl=ttl,
                      client=get_etcd_client(),
                      log_ctx=log_ctx, timeout=timeout, op=op)


def refresh_lock(lock, log_ctx=LOG):
    if not lock.is_acquired():
        log_ctx.with_fields({'lock': lock.name}).info(
            'Attempt to refresh an expired lock')
        raise exceptions.LockException(
            'The lock on %s has expired.' % lock.path)

    lock.refresh()


def refresh_locks(locks):
    if locks:
        for lock in locks:
            if lock:
                refresh_lock(lock)


@retry_etcd_forever
def clear_stale_locks():
    # Remove all locks held by former processes on this node. This is required
    # after an unclean restart, otherwise we need to wait for these locks to
    # timeout and that can take a long time.
    client = get_etcd_client()

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
    for value in get_etcd_client().get_prefix(LOCK_PREFIX + '/'):
        key_val[value[1]['key'].decode('utf-8')] = json.loads(value[0])
    return key_val


def _construct_key(objecttype, subtype, name):
    if subtype and name:
        return f'/sf/{objecttype}/{subtype}/{name}'
    if name:
        return f'/sf/{objecttype}/{name}'
    if subtype:
        return f'/sf/{objecttype}/{subtype}/'
    return '/sf/%s/' % objecttype


class JSONEncoderCustomTypes(json.JSONEncoder):
    def default(self, obj):
        if QueueTask.__subclasscheck__(type(obj)):
            return obj.obj_dict()
        if type(obj) is baseobject.State:
            return obj.obj_dict()
        return json.JSONEncoder.default(self, obj)


@retry_etcd_forever
def put_raw(path, data):
    encoded = json.dumps(data, indent=4, sort_keys=True,
                         cls=JSONEncoderCustomTypes)
    get_etcd_client().put(path, encoded, lease=None)
    LOG.info('etcd put %s' % path)


@retry_etcd_forever
def put(objecttype, subtype, name, data):
    path = _construct_key(objecttype, subtype, name)
    put_raw(path, data)


@retry_etcd_forever
def create(objecttype, subtype, name, data):
    path = _construct_key(objecttype, subtype, name)
    encoded = json.dumps(data, indent=4, sort_keys=True,
                         cls=JSONEncoderCustomTypes)
    LOG.info('etcd create %s' % path)
    return get_etcd_client().create(path, encoded, lease=None)


@retry_etcd_forever
def get_raw(path):
    value = get_etcd_client().get(path)
    if value is None or len(value) == 0:
        return None
    return json.loads(value[0])


@retry_etcd_forever
def get(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    return get_raw(path)


@retry_etcd_forever
def get_prefix(path, sort_order=None, sort_target='key', limit=0):
    for data, metadata in get_etcd_client().get_prefix(
            path, sort_order=sort_order, sort_target='key', limit=limit):
        yield str(metadata['key'].decode('utf-8')), json.loads(data)


def get_all(objecttype, subtype, prefix=None, sort_order=None, limit=0):
    path = _construct_key(objecttype, subtype, prefix)
    return get_prefix(path, sort_order=sort_order, sort_target='key', limit=limit)


@retry_etcd_forever
def get_all_dict(objecttype, subtype=None, sort_order=None, limit=0):
    path = _construct_key(objecttype, subtype, None)
    key_val = {}

    for value in get_etcd_client().get_prefix(
            path, sort_order=sort_order, sort_target='key', limit=limit):
        key_val[value[1]['key'].decode('utf-8')] = json.loads(value[0])

    return key_val


@retry_etcd_forever
def delete_raw(path):
    get_etcd_client().delete(path)
    LOG.info('etcd delete %s' % path)


def delete(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    delete_raw(path)


@retry_etcd_forever
def delete_all(objecttype, subtype):
    path = _construct_key(objecttype, subtype, None)
    get_etcd_client().delete_prefix(path)


@retry_etcd_forever
def delete_prefix(path):
    get_etcd_client().delete_prefix(path)
    LOG.info('etcd deleteprefix %s' % path)


def enqueue(queuename, workitem, delay=0):
    entry_time = time.time() + delay
    jobname = f'{entry_time}-{util_random.random_id()}'
    put('queue', queuename, jobname, workitem)
    LOG.with_fields({
        'jobname': jobname,
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
    client = get_etcd_client()

    for data, metadata in client.get_prefix(queue_path, sort_order='ascend',
                                            sort_target='key', limit=1):
        jobname = str(metadata['key']).split('/')[-1].rstrip("'")

        # Ensure that this task isn't in the future
        if float(jobname.split('-')[0]) > time.time():
            return None

        workitem = json.loads(data, object_hook=decodeTasks)
        put('processing', queuename, jobname, workitem)
        client.delete(metadata['key'])
        LOG.with_fields({
            'jobname': jobname,
            'queuename': queuename,
            'workitem': workitem,
            }).info('Moved workitem from queue to processing')

        return jobname, workitem

    return None


def resolve(queuename, jobname):
    delete('processing', queuename, jobname)
    LOG.with_fields({
        'jobname': jobname,
        'queuename': queuename,
        }).info('Resolved workitem')


def get_queue_length(queuename):
    queued = 0
    deferred = 0
    for name, _ in get_all('queue', queuename):
        if float(name.split('/')[-1].split('-')[0]) > time.time():
            deferred += 1
        else:
            queued += 1

    processing = len(list(get_all('processing', queuename)))
    return processing, queued, deferred


@retry_etcd_forever
def _restart_queue(queuename):
    queue_path = _construct_key('processing', queuename, None)

    for data, metadata in get_etcd_client().get_prefix(
            queue_path, sort_order='ascend'):
        jobname = str(metadata['key']).split('/')[-1].rstrip("'")
        workitem = json.loads(data)
        put('queue', queuename, jobname, workitem)
        delete('processing', queuename, jobname)
        LOG.with_fields({
            'jobname': jobname,
            'queuename': queuename,
            }).warning('Reset workitem')


def get_outstanding_jobs():
    for data, metadata in get_etcd_client().get_prefix('/sf/processing'):
        yield metadata['key'].decode('utf-8'), json.loads(data, object_hook=decodeTasks)
    for data, metadata in get_etcd_client().get_prefix('/sf/queued'):
        yield metadata['key'].decode('utf-8'), json.loads(data, object_hook=decodeTasks)


def get_current_blob_transfers(absent_nodes=[]):
    current_fetches = defaultdict(list)
    for workname, workitem in get_outstanding_jobs():
        # A workname looks like: /sf/queue/sf-3/jobname
        _, _, phase, node, _ = workname.split('/')
        if node == 'networknode':
            continue

        for task in workitem:
            if isinstance(task, FetchBlobTask):
                if node in absent_nodes:
                    LOG.with_fields({
                        'blob': task.blob_uuid,
                        'node': node,
                        'phase': phase
                    }).warning('Node is absent, ignoring fetch')
                else:
                    LOG.with_fields({
                        'blob': task.blob_uuid,
                        'node': node,
                        'phase': phase
                    }).info('Node is fetching blob')
                    current_fetches[task.blob_uuid].append(node)

    return current_fetches


def restart_queues():
    # Move things which were in processing back to the queue because
    # we didn't complete them before crashing.
    if config.NODE_IS_NETWORK_NODE:
        _restart_queue('networknode')
    _restart_queue(config.NODE_NAME)
