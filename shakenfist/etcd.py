import json
import os
import psutil
import time

from etcd3gw.client import Etcd3Client
from etcd3gw.lock import Lock

from shakenfist import config
from shakenfist import db
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist import util
from shakenfist.tasks import QueueTask


####################################################################
# Please do not call this file directly, but instead call it via   #
# the db.py abstraction.                                           #
####################################################################


LOG, _ = logutil.setup(__name__)


class ActualLock(Lock):
    def __init__(self, objecttype, subtype, name, ttl=120,
                 client=None, timeout=None, log_ctx=LOG):

        self.path = _construct_key(objecttype, subtype, name)
        super(ActualLock, self).__init__(self.path, ttl=ttl, client=client)

        self.objecttype = objecttype
        self.objectname = name
        self.timeout = min(timeout, 1000000000)
        self.log_ctx = log_ctx.withField('path', self.path)

        # We override the UUID of the lock with something more helpful to debugging
        self._uuid = json.dumps(
            {
                'node': config.parsed.get('NODE_NAME'),
                'pid': os.getpid()
            },
            indent=4, sort_keys=True)

        # We also override the location of the lock so that we're in our own spot
        self.key = '/sflocks%s' % self.path

    def get_holder(self):
        value = Etcd3Client().get(self.key, metadata=True)
        if value is None or len(value) == 0:
            return None, NotImplementedError

        if not value[0][0]:
            return None, None

        d = json.loads(value[0][0])
        return d['node'], d['pid']

    def __enter__(self):
        start_time = time.time()
        slow_warned = False
        threshold = int(config.parsed.get('SLOW_LOCK_THRESHOLD'))

        try:
            while time.time() - start_time < self.timeout:
                res = self.acquire()
                if res:
                    return self

                duration = time.time() - start_time
                if (duration > threshold and not slow_warned):
                    db.add_event(self.objecttype, self.objectname,
                                 'lock', 'acquire', None,
                                 'Waiting for lock more than threshold')

                    node, pid = self.get_holder()
                    self.log_ctx.withFields({'duration': duration,
                                             'threshold': threshold,
                                             'holder-pid': pid,
                                             'holder-node': node,
                                             }).info('Waiting for lock')
                    slow_warned = True

                time.sleep(1)

            duration = time.time() - start_time
            db.add_event(self.objecttype, self.objectname,
                         'lock', 'failed', None,
                         'Failed to acquire lock after %.02f seconds' % duration)

            node, pid = self.get_holder()
            self.log_ctx.withFields({'duration': duration,
                                     'holder-pid': pid,
                                     'holder-node': node,
                                     }).info('Failed to acquire lock')

            raise exceptions.LockException(
                'Cannot acquire lock %s, timed out after %.02f seconds'
                % (self.name, duration))

        finally:
            duration = time.time() - start_time
            if duration > threshold:
                db.add_event(self.objecttype, self.objectname,
                             'lock', 'acquired', None,
                             'Waited %d seconds for lock' % duration)
                self.log_ctx.withFields({'duration': duration,
                                         }).info('Acquiring a lock was slow')

    def __exit__(self, _exception_type, _exception_value, _traceback):
        if not self.release():
            raise exceptions.LockException(
                'Cannot release lock: %s' % self.name)
        return self


def get_lock(objecttype, subtype, name, ttl=60, timeout=10, log_ctx=LOG):
    """Retrieves an etcd lock object. It is not locked, to lock use acquire().

    The returned lock can be used as a context manager, with the lock being
    acquired on entry and released on exit. Note that the lock acquire process
    will have no timeout.
    """
    return ActualLock(objecttype, subtype, name, ttl=ttl, client=Etcd3Client(),
                      log_ctx=log_ctx, timeout=timeout)


def refresh_lock(lock, log_ctx=LOG):
    if not lock.is_acquired():
        raise exceptions.LockException(
            'The lock on %s has expired.' % lock.path)

    lock.refresh()
    log_ctx.withField('lock', lock.name).info('Refreshed lock')


def clear_stale_locks():
    # Remove all locks held by former processes on this node. This is required
    # after an unclean restart, otherwise we need to wait for these locks to
    # timeout and that can take a long time.
    client = Etcd3Client()

    for data, metadata in client.get_prefix('/sflocks/', sort_order='ascend', sort_target='key'):
        lockname = str(metadata['key']).replace('/sflocks/', '')
        holder = json.loads(data)
        node = holder['node']
        pid = int(holder['pid'])

        if node == config.parsed.get('NODE_NAME') and not psutil.pid_exists(pid):
            client.delete(metadata['key'])
            LOG.withFields({'lock': lockname,
                            'old-pid': pid,
                            'old-node': node,
                            }).warning('Removed stale lock')


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
            return obj.json_dump()
        return json.JSONEncoder.default(self, obj)


def put(objecttype, subtype, name, data, ttl=None):
    # TODO(andy) Until we fix exception logging in this module
    try:
        path = _construct_key(objecttype, subtype, name)
        encoded = json.dumps(data, indent=4, sort_keys=True, cls=JSONEncoderTasks)
        Etcd3Client().put(path, encoded, lease=None)
    except Exception:
        LOG.exception('etcd.put()')


def get(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    value = Etcd3Client().get(path, metadata=True)
    if value is None or len(value) == 0:
        return None
    return json.loads(value[0][0])


def get_all(objecttype, subtype, sort_order=None):
    path = _construct_key(objecttype, subtype, None)
    for value in Etcd3Client().get_prefix(path, sort_order=sort_order):
        yield json.loads(value[0])


def delete(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    Etcd3Client().delete(path)


def delete_all(objecttype, subtype, sort_order=None):
    path = _construct_key(objecttype, subtype, None)
    Etcd3Client().delete_prefix(path)


def enqueue(queuename, workitem):
    with get_lock('queue', None, queuename):
        i = 0
        entry_time = time.time()
        jobname = '%s-%03d' % (entry_time, i)

        while get('queue', queuename, jobname):
            i += 1
            jobname = '%s-%03d' % (entry_time, i)

        put('queue', queuename, jobname, workitem)
        LOG.withFields({'jobname': jobname,
                        'queuename': queuename,
                        'workitem': workitem,
                        }).info('Enqueued workitem')


def decodeTasks(json_dict):
    if 'tasks' not in json_dict:
        return json_dict

    def _all_subclasses(cls):
        all = cls.__subclasses__()
        for sc in cls.__subclasses__():
            all += _all_subclasses(sc)
        return all

    task_list = []
    for task_item in json_dict['tasks']:
        item = task_item
        for task_class in _all_subclasses(QueueTask):
            if task_class.name() and task_item.get('task') == task_class.name():
                del task_item['task']
                # This is where new QueueTask subclass versions should be handled
                del task_item['version']
                item = task_class(**task_item)
                break
        task_list.append(item)

    return {'tasks': task_list}


def dequeue(queuename):
    # TODO(andy) why are exception in here not logged?? logging works but exceptions evaporate

    queue_path = _construct_key('queue', queuename, None)
    client = Etcd3Client()

    with get_lock('queue', None, queuename):
        for data, metadata in client.get_prefix(queue_path, sort_order='ascend', sort_target='key'):
            jobname = str(metadata['key']).split('/')[-1].rstrip("'")
            # TODO(andy) Until we fix exception logging in this module
            try:
                workitem = json.loads(data, object_hook=decodeTasks)
            except Exception:
                LOG.exception('etcd.dequeue()')

            put('processing', queuename, jobname, workitem)
            client.delete(metadata['key'])
            LOG.withFields({'jobname': jobname,
                            'queuename': queuename,
                            'workitem': workitem,
                            }).info('Moved workitem from queue to processing')

            return jobname, workitem

    return None, None


def resolve(queuename, jobname):
    with get_lock('queue', None, queuename):
        delete('processing', queuename, jobname)
        LOG.withFields({'jobname': jobname,
                        'queuename': queuename,
                        }).info('Resolved workitem')


def get_queue_length(queuename):
    with get_lock('queue', None, queuename):
        queued = len(list(get_all('queue', queuename)))
        processing = len(list(get_all('processing', queuename)))
        return processing, queued


def _restart_queue(queuename):
    queue_path = _construct_key('processing', queuename, None)
    with get_lock('queue', None, queuename):
        for data, metadata in Etcd3Client().get_prefix(queue_path, sort_order='ascend'):
            jobname = str(metadata.key).split('/')[-1].rstrip("'")
            workitem = json.loads(data)
            put('queue', queuename, jobname, workitem)
            delete('processing', queuename, jobname)
            LOG.withFields({'jobname': jobname,
                            'queuename': queuename,
                            }).warning('Reset workitem')


def restart_queues():
    # Move things which were in processing back to the queue because
    # we didn't complete them before crashing.
    if util.is_network_node():
        _restart_queue('networknode')
    _restart_queue(config.parsed.get('NODE_NAME'))
