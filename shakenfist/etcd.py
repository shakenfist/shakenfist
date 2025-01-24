import json
import os
import threading
import time

from etcd3gw.client import Etcd3Client
from etcd3gw.exceptions import InternalServerError
from etcd3gw.lock import Lock
from google.rpc import error_details_pb2
import grpc
from grpc_status import rpc_status
import psutil
import requests
from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities import random as util_random  # noreorder

from shakenfist import etcd_pb2
from shakenfist import etcd_pb2_grpc
from shakenfist import exceptions
from shakenfist.config import config
from shakenfist.util import callstack as util_callstack
from shakenfist.util import json as util_json


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
local.sf_etcd_native_client = None


def get_etcd_client():
    c = getattr(local, 'sf_etcd_client', None)
    if not c:
        LOG.info('Creating new etcd client via gateway')
        c = local.sf_etcd_client = WrappedEtcdClient()
    return c


def reset_client():
    local.sf_etcd_client = None


def get_etcd_native_client():
    # If your eventlog server isn't setup, we get cranky. Note that this
    # happens during unit test discovery for py3 unit tests.
    if not config.ETCD_HOST:
        caller = util_callstack.generate_traceback()
        LOG.error('Cannot communicate with etcd, no configured server! Caller was:\n'
                  f'{caller}')
        return

    c = getattr(local, 'sf_etcd_native_client', None)
    if c:
        # Ensure the channel is ready
        try:
            grpc.channel_ready_future(c).result(timeout=0.5)
        except grpc.FutureTimeoutError:
            # We do not close the channel here because this cause grpc to sometimes
            # throw a traceback from another thread trying to monitor a now closed
            # channel.
            c = None

    if not c:
        if config.LOG_ETCD_CONNECTIONS:
            LOG.info('Creating new etcd client via native protocol')
        local.sf_etcd_native_client = grpc.insecure_channel(
            '%s:2379' % config.ETCD_HOST,
            options=[
                ('keepalive_timeout_ms', 200),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.keepalive_permit_without_calls', 1),
            ]
        )
        c = local.sf_etcd_native_client
    return c


def reset_native_client():
    # We do not close the channel here because this cause grpc to sometimes
    # throw a traceback from another thread trying to monitor a now closed
    # channel.
    local.sf_etcd_native_client = None


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
        attempt = 0
        while True:
            try:
                return func(*args, **kwargs)
            except InternalServerError as e:
                LOG.with_fields(kwargs).with_fields({
                    'args': args,
                    'function': func,
                    'attempt': attempt
                }).info('Failed etcd request via gateway')
                LOG.error('Etcd3gw Internal Server Error: %s' % e)
            time.sleep(attempt / 10.0)
            attempt += 1
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

        self.node = config.NODE_NAME
        self.pid = os.getpid()
        caller = util_callstack.get_caller(offset=3)

        # We also override the location of the lock so that we're in our own spot
        self.key = LOCK_PREFIX + self.path

        self.log_ctx = log_ctx.with_fields(
            {
                'lock': self.path,
                'key': self.key,
                'node': self.node,
                'pid': self.pid,
                'thread': threading.get_ident(),
                'line': caller,
                'operation': self.operation,
                'id': self.lockid
            })

        # We override the UUID of the lock with something more helpful to debugging
        self._uuid = json.dumps(
            {
                'node': self.node,
                'pid': self.pid,
                'thread': threading.get_ident(),
                'line': caller,
                'operation': self.operation,
                'id': self.lockid
            },
            indent=4, sort_keys=True)

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

    def get_lease(self):
        return self.lease

    def refresh(self):
        super().refresh()
        self.log_ctx.info('Refreshed lock')

    def __enter__(self):
        start_time = time.time()
        slow_warned = False
        threshold = self.timeout / 2

        while time.time() - start_time < self.timeout:
            res = self.acquire()
            self.log_ctx = self.log_ctx.with_fields({
                'leased_keys': self.get_lease().keys()
            })

            duration = round(time.time() - start_time, 2)
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
            'duration': round(time.time() - start_time, 2)
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
                    'path': self.path,
                    'name': self.name,
                    'key': self.key,
                    'attempt': attempts,
                    'leased_keys': self.get_lease().keys()
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
    log = log_ctx.with_fields({
        'lock': lock.name,
        'path': lock.path,
        'pid': lock.pid
    })

    try:
        psutil.Process(lock.pid)
        if not lock.is_acquired():
            log.error('Attempt to refresh an expired lock')
            raise exceptions.LockException(
                f'The lock on {lock.path} has expired.')
    except (psutil.NoSuchProcess, FileNotFoundError):
        log.error('Attempt to refresh lock whose process has disappeared')
        raise exceptions.LockException(
            f'The process (pid {lock.pid}) holding lock {lock.path} has disappeared.')

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
    for key, holder in get_prefix_raw(LOCK_PREFIX + '/'):
        lockname = key.replace(LOCK_PREFIX + '/', '')
        node = holder['node']
        pid = int(holder['pid'])

        if node == config.NODE_NAME and not psutil.pid_exists(pid):
            delete_raw(key)
            LOG.with_fields({'lock': lockname,
                             'old-pid': pid,
                             'old-node': node,
                             }).warning('Removed stale lock')


@retry_etcd_forever
def get_existing_locks():
    key_val = {}
    for key, holder in get_prefix_raw(LOCK_PREFIX + '/'):
        key_val[key] = holder
    return key_val


def _construct_key(objecttype, subtype, name):
    if subtype and name:
        return f'/sf/{objecttype}/{subtype}/{name}'
    if name:
        return f'/sf/{objecttype}/{name}'
    if subtype:
        return f'/sf/{objecttype}/{subtype}/'
    return f'/sf/{objecttype}/'


def put(objecttype, subtype, name, data):
    path = _construct_key(objecttype, subtype, name)
    put_raw(path, data)


def create(objecttype, subtype, name, data):
    path = _construct_key(objecttype, subtype, name)
    return create_raw(path, data)


def get(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    return get_raw(path)


def get_all(objecttype, subtype, prefix=None, limit=0):
    path = _construct_key(objecttype, subtype, prefix)
    return get_prefix_raw(path, limit=limit)


def get_all_dict(objecttype, subtype=None, limit=0):
    path = _construct_key(objecttype, subtype, None)
    key_val = {}

    for key, value in get_prefix_raw(path, limit=limit):
        key_val[key] = value

    return key_val


def replace(objecttype, subtype, name, original_data, new_data):
    return replace_many_raw([{
        'path': _construct_key(objecttype, subtype, name),
        'original_data': original_data,
        'new_data': new_data
    }])[0]


def delete(objecttype, subtype, name):
    path = _construct_key(objecttype, subtype, name)
    return delete_raw(path)


@retry_etcd_forever
def delete_all(objecttype, subtype, name=None):
    path = _construct_key(objecttype, subtype, name)
    get_etcd_client().delete_prefix(path)


@retry_etcd_forever
def delete_prefix(path):
    get_etcd_client().delete_prefix(path)
    LOG.info('etcd deleteprefix %s' % path)


@retry_etcd_forever
def enqueue(queuename, workitem, delay=0):
    # This might retry if we clash with another enqueue at literally (to the
    # millisecond) the same time. It should be unlikely?
    attempts = 0

    while attempts < 3:
        entry_time = time.time() + delay
        jobname = f'{entry_time}-{util_random.random_id()}'
        if create_raw(f'/sf/queue/{queuename}/{jobname}', workitem):
            LOG.with_fields({
                'jobname': jobname,
                'queuename': queuename,
                'workitem': workitem,
                'attempt': attempts
            }).info('Enqueued workitem')
            return

        attempts += 1

    LOG.with_fields({
        'jobname': jobname,
        'queuename': queuename,
        'workitem': workitem,
        'attempt': attempts
    }).error('Repeated attempts to enqueue workitem failed')
    raise exceptions.CannotEnqueueWork(
        'Repeated attempts to enqueue workitem failed')


def dequeue(queuename):
    queue_path = _construct_key('queue', queuename, None)
    for path, workitem in get_prefix_raw(queue_path, limit=1):
        jobname = path.split('/')[-1]

        # Ensure that this task isn't in the future
        if float(jobname.split('-')[0]) > time.time():
            return None

        # Attempt to claim the job in a transaction
        new_path = path.replace('queue', 'processing')
        success = replace_many_raw([
            {
                'path': path,
                'original_data': workitem,
                'new_data': None
            },
            {
                'path': new_path,
                'original_data': None,
                'new_data': workitem
            },
        ])
        if success:
            LOG.with_fields({
                'jobname': jobname,
                'queuename': queuename,
                'workitem': workitem,
            }).info('Moved workitem from queue to processing')
            return jobname, workitem
        else:
            LOG.with_fields({
                'jobname': jobname,
                'queuename': queuename,
                'workitem': workitem,
            }).debug(
                'Failed to move workitem from queue to processing. '
                'Possibly racing another worker?')

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


def restart_queue(queuename):
    queue_path = _construct_key('processing', queuename, None)

    for path, workitem in get_prefix_raw(queue_path):
        jobname = path.split('/')[-1]
        put('queue', queuename, jobname, workitem)
        delete('processing', queuename, jobname)
        LOG.with_fields({
            'jobname': jobname,
            'queuename': queuename,
            }).warning('Reset workitem')


# Direct etcd calls via gRPC
def _log_and_raise_error(rpc_error):
    code = None
    detail = None

    status = rpc_status.from_call(rpc_error)
    if status:
        code = status.code
        if status.details:
            detail = []
            for d in status.details:
                if d.Is(error_details_pb2.QuotaFailure.DESCRIPTOR):
                    info = error_details_pb2.QuotaFailure()
                    d.Unpack(info)
                    raise exceptions.gRPCException(f'Quota failure: {info}')
                detail.append(d)
    else:
        try:
            code = rpc_error.code()
            detail = rpc_error.detail()
        except AttributeError:
            ...

    if not detail:
        detail = 'no detail available'

    if code == grpc.StatusCode.UNAVAILABLE:
        # Unavailable such as "sendmsg: Socket operation on non-socket"
        raise exceptions.gRPCException(f'Server unavailable: {detail}')

    if code == grpc.StatusCode.ABORTED:
        raise exceptions.gRPCException(f'Aborted: {detail}')

    if code == grpc.StatusCode.INTERNAL:
        raise exceptions.gRPCException(f'Internal error: {detail}')

    if code == 32:
        # "sendmsg: Broken pipe (32)"
        raise exceptions.gRPCEXception(f'Broken pipe: {detail}')

    LOG.debug(f'Unhandled gRPC call failure: {rpc_error}')
    raise exceptions.gRPCException(rpc_error)


def _retry_etcd_native_client(func):
    def wrapper(*args, **kwargs):
        attempt = 0
        last_exception = None

        while attempt < 3:
            try:
                return func(*args, **kwargs)
            except exceptions.gRPCException as e:
                last_exception = e

            if attempt > 0:
                LOG.with_fields(kwargs).with_fields({
                    'args': args,
                    'function': func,
                    'attempt': attempt
                }).info('Failed etcd request via native protocol')
            reset_native_client()
            time.sleep(attempt / 10.0)
            attempt += 1

        if last_exception:
            raise last_exception

    return wrapper


@_retry_etcd_native_client
def compact(revision):
    channel = get_etcd_native_client()
    stub = etcd_pb2_grpc.KVStub(channel)
    try:
        stub.Compact(etcd_pb2.CompactionRequest(
            revision=revision, physical=True
        )
        )
    except grpc.RpcError as rpc_error:
        _log_and_raise_error(rpc_error)

    try:
        stub = etcd_pb2_grpc.MaintenanceStub(channel)
        request = etcd_pb2.DefragmentRequest()
        stub.Defragment(request)
    except grpc.RpcError as rpc_error:
        _log_and_raise_error(rpc_error)
        return False


@_retry_etcd_native_client
def get_raw(path):
    path_encoded = path.encode()
    channel = get_etcd_native_client()
    stub = etcd_pb2_grpc.KVStub(channel)

    try:
        resp = stub.Range(
            etcd_pb2.RangeRequest(
                key=path_encoded
            )
        )
    except grpc.RpcError as rpc_error:
        _log_and_raise_error(rpc_error)

    if len(resp.kvs) > 0:
        kvs = resp.kvs[0]
        return json.loads(kvs.value.decode())
    return None


@_retry_etcd_native_client
def get_prefix_raw(path, limit=0):
    path_encoded = path.encode()

    # From the etcd API docs: "If range_end is key plus one (e.g.,
    # "aa"+1 == "ab", "a\xff"+1 == "b"), then the range represents all keys
    # prefixed with key."
    #
    #     https://etcd.io/docs/v3.3/learning/api/#key-ranges
    #
    # Note that this implementation assumes our keys are basically ASCII, that
    # is that we will never have a last byte of 0xFF (because we'd have to
    # carry if we did).
    range_end = bytearray(path_encoded)
    range_end[-1] = range_end[-1] + 1
    range_end = bytes(range_end)

    channel = get_etcd_native_client()
    stub = etcd_pb2_grpc.KVStub(channel)

    try:
        resp = stub.Range(
            etcd_pb2.RangeRequest(
                key=path_encoded,
                range_end=range_end,
                limit=limit,
                sort_order=etcd_pb2.RangeRequest.ASCEND,
                sort_target=etcd_pb2.RangeRequest.KEY
            )
        )
    except grpc.RpcError as rpc_error:
        _log_and_raise_error(rpc_error)

    for kvs in resp.kvs:
        yield kvs.key.decode(), json.loads(kvs.value.decode())
    return None


@_retry_etcd_native_client
def put_raw(path, new_data):
    path_encoded = path.encode()
    new_data_encoded = util_json.json_dump(new_data).encode()
    channel = get_etcd_native_client()
    stub = etcd_pb2_grpc.KVStub(channel)

    # NOTE(mikal): yes, this doesn't return a meaningful result. etcd3gw
    # simply hardcoded a "return True" here, the etcd server returns a
    # result indicating the previous value of the key. That is, a failure
    # will raise an exception.
    try:
        stub.Put(
            etcd_pb2.PutRequest(
                key=path_encoded,
                value=new_data_encoded
            )
        )
    except grpc.RpcError as rpc_error:
        _log_and_raise_error(rpc_error)


@_retry_etcd_native_client
def delete_raw(path):
    path_encoded = path.encode()
    channel = get_etcd_native_client()
    stub = etcd_pb2_grpc.KVStub(channel)

    try:
        resp = stub.DeleteRange(
            etcd_pb2.DeleteRangeRequest(
                key=path_encoded
            )
        )
    except grpc.RpcError as rpc_error:
        _log_and_raise_error(rpc_error)

    return resp.deleted == 1


def create_raw(path, new_data):
    return replace_many_raw([{
        'path': path,
        'original_data': None,
        'new_data': new_data
    }])[0]


def replace_raw(path, original_data, new_data):
    return replace_many_raw([{
        'path': path,
        'original_data': original_data,
        'new_data': new_data
    }])[0]


def transactional_delete_raw(path, original_data):
    return replace_many_raw([{
        'path': path,
        'original_data': original_data,
        'new_data': None
    }])[0]


# NOTE(mikal): note that mutations are expected to use strings in their
# descriptions, not bytes.
@_retry_etcd_native_client
def replace_many_raw(mutations):
    original_values_by_path = {}
    new_values_by_path = {}

    comparisons = []
    replacements = []
    failures = []

    for mutation in mutations:
        path_encoded = mutation['path'].encode()
        original_data_encoded = util_json.json_dump(
            mutation['original_data']).encode()
        new_data_encoded = util_json.json_dump(
            mutation['new_data']).encode()

        if mutation['original_data'] is None:
            comparisons.append(
                etcd_pb2.Compare(
                    key=path_encoded,
                    result=etcd_pb2.Compare.EQUAL,
                    target=etcd_pb2.Compare.CREATE,
                    create_revision=0
                )
            )
            original_values_by_path[path_encoded] = None
        else:
            comparisons.append(
                etcd_pb2.Compare(
                    key=path_encoded,
                    result=etcd_pb2.Compare.EQUAL,
                    target=etcd_pb2.Compare.VALUE,
                    value=original_data_encoded
                )
            )
            original_values_by_path[path_encoded] = original_data_encoded

        if mutation['new_data'] is None:
            replacements.append(
                etcd_pb2.RequestOp(
                    request_delete_range=etcd_pb2.DeleteRangeRequest(
                        key=path_encoded
                    )
                )
            )
            new_values_by_path[path_encoded] = None
        else:
            replacements.append(
                etcd_pb2.RequestOp(
                    request_put=etcd_pb2.PutRequest(
                        key=path_encoded,
                        value=new_data_encoded
                    )
                )
            )
            new_values_by_path[path_encoded] = new_data_encoded

        # On failure, we grab all of the keys and their current values
        failures.append(
            etcd_pb2.RequestOp(
                request_range=etcd_pb2.RangeRequest(
                    key=path_encoded
                )
            )
        )

    channel = channel = get_etcd_native_client()
    stub = etcd_pb2_grpc.KVStub(channel)
    try:
        response = stub.Txn(
            etcd_pb2.TxnRequest(
                compare=comparisons,
                success=replacements,
                failure=failures
            )
        )
    except grpc.RpcError as rpc_error:
        _log_and_raise_error(rpc_error)

    if response.succeeded:
        return True, []

    # Determine which keys had non-matching values
    failures = []
    for resp in response.responses:
        if resp.HasField('response_range'):
            for kvs in resp.response_range.kvs:
                if original_values_by_path[kvs.key] != kvs.value:
                    failures.append(
                        {
                            'path': kvs.key,
                            'desired': original_values_by_path[kvs.key],
                            'actual': kvs.value,
                            'replacement': new_values_by_path[kvs.key]
                        }
                    )
                    del original_values_by_path[kvs.key]

    for key in original_values_by_path:
        failures.append(
            {
                'path': key,
                'desired': original_values_by_path[key],
                'actual': None,
                'replacement': new_values_by_path[key]
            }
        )

    LOG.with_fields({'failed': failures}).info('Transaction failure')
    return False, failures
