"""Minimal etcd access retained for DATA_MIGRATIONS drain entries.

This module is kept so that operators upgrading from a previous version
can drain residual etcd keys on first startup. It will be removed in the
next minor version. Do NOT add new callers — use MariaDB instead.

The MAC address reservation path in network/interface.py still writes
to etcd as a temporary measure until a MariaDB-native MAC reservation
is added.
"""
import json
import threading
import time
from typing import Any, Generator, Optional

from etcd3gw.client import Etcd3Client
from etcd3gw.exceptions import InternalServerError
from google.rpc import error_details_pb2
import grpc
from grpc_status import rpc_status
import requests
from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist.config import config
from shakenfist.protos import etcd_pb2
from shakenfist.protos import etcd_pb2_grpc
from shakenfist.util import callstack as util_callstack
from shakenfist.util import json as util_json


LOG, _ = logs.setup(__name__)


class WrappedEtcdClient(Etcd3Client):
    def __init__(self, host=None, port=2379, protocol='http',
                 ca_cert=None, cert_key=None, cert_cert=None, timeout=None,
                 api_path='/v3beta/'):
        if not host:
            host = config.ETCD_HOST

        if api_path == '/v3alpha':
            raise Exception('etcd3 v3alpha endpoint is known not to work')

        self.ca_cert = ca_cert
        self.cert_key = cert_key
        self.cert_cert = cert_cert
        self.timeout = timeout

        super().__init__(
            host=host, port=port, protocol=protocol, ca_cert=ca_cert,
            cert_key=cert_key, cert_cert=cert_cert, timeout=timeout,
            api_path=api_path)

        self.session.trust_env = False

    def post(self, *args, **kwargs):
        try:
            return super().post(*args, **kwargs)
        except Exception as e:
            LOG.info('Retrying after receiving etcd error: %s' % e)

            self.session = requests.Session()
            self.session.trust_env = False
            if self.timeout is not None:
                self.session.timeout = self.timeout
            if self.ca_cert is not None:
                self.session.verify = self.ca_cert
            if self.cert_cert is not None and self.cert_key is not None:
                self.session.cert = (self.cert_cert, self.cert_key)
            return super().post(*args, **kwargs)


local = threading.local()
local.sf_etcd_client = None
local.sf_etcd_native_client = None


def get_etcd_client():
    c = getattr(local, 'sf_etcd_client', None)
    if not c:
        LOG.info('Creating new etcd client via gateway')
        c = local.sf_etcd_client = WrappedEtcdClient()
    return c


def get_etcd_native_client():
    if not config.ETCD_HOST:
        caller = util_callstack.generate_traceback()
        LOG.error('Cannot communicate with etcd, no configured server! Caller was:\n'
                  f'{caller}')
        return

    c = getattr(local, 'sf_etcd_native_client', None)
    if c:
        try:
            grpc.channel_ready_future(c).result(timeout=0.5)
        except grpc.FutureTimeoutError:
            c = None

    if not c:
        local.sf_etcd_native_client = grpc.insecure_channel(
            '%s:2379' % config.ETCD_HOST,
            options=[
                ('keepalive_timeout_ms', 200),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.keepalive_permit_without_calls', 1),
                ('grpc.max_send_message_length', 100000000),
                ('grpc.max_receive_message_length', 100000000),
            ]
        )
        c = local.sf_etcd_native_client
    return c


def _reset_native_client():
    local.sf_etcd_native_client = None


def retry_etcd_forever(func):
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


def _construct_key(objecttype, subtype, name, prefix='sf'):
    if subtype and name:
        return f'/{prefix}/{objecttype}/{subtype}/{name}'
    if name:
        return f'/{prefix}/{objecttype}/{name}'
    if subtype:
        return f'/{prefix}/{objecttype}/{subtype}/'
    return f'/{prefix}/{objecttype}/'


def put(objecttype, subtype, name, data):
    path = _construct_key(objecttype, subtype, name)
    put_raw(path, data)


def create(objecttype, subtype, name, data):
    path = _construct_key(objecttype, subtype, name)
    return create_raw(path, data)


def get(
        objecttype: str, subtype: Optional[str], name: Optional[str]
) -> Optional[dict[str, Any]]:
    path = _construct_key(objecttype, subtype, name)
    return get_raw(path)


def get_all(
        objecttype: str, subtype: Optional[str],
        prefix: Optional[str] = None, limit: int = 0
) -> Generator[tuple[str, dict[str, Any]], None, None]:
    path = _construct_key(objecttype, subtype, prefix)
    return get_prefix_raw(path, limit=limit)


def get_all_dict(objecttype, subtype=None, limit=0):
    path = _construct_key(objecttype, subtype, None)
    key_val = {}

    for key, value in get_prefix_raw(path, limit=limit):
        key_val[key] = value

    return key_val


def delete(objecttype: str, subtype: Optional[str], name: str) -> bool:
    path = _construct_key(objecttype, subtype, name)
    return delete_raw(path)


@retry_etcd_forever
def delete_all(objecttype, subtype, name=None):
    path = _construct_key(objecttype, subtype, name)
    get_etcd_client().delete_prefix(path)


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
        raise exceptions.gRPCException(f'Server unavailable: {detail}')

    if code == grpc.StatusCode.ABORTED:
        raise exceptions.gRPCException(f'Aborted: {detail}')

    if code == grpc.StatusCode.INTERNAL:
        raise exceptions.gRPCException(f'Internal error: {detail}')

    if code == 32:
        raise exceptions.gRPCException(f'Broken pipe: {detail}')

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
            _reset_native_client()
            time.sleep(attempt / 10.0)
            attempt += 1

        if last_exception:
            raise last_exception

    return wrapper


@_retry_etcd_native_client
def get_raw(path: str) -> Optional[dict[str, Any]]:
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
def get_prefix_raw(
        path: str, limit: int = 0
) -> Generator[tuple[str, dict[str, Any]], None, None]:
    path_encoded = path.encode()

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
def delete_raw(path: str) -> bool:
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
    return replace_many_raw(
        [
            {
                'path': path,
                'original_data': None,
                'new_data': new_data
            }
        ],
        suppress_failure_audit=True
    )[0]


@_retry_etcd_native_client
def replace_many_raw(mutations, suppress_failure_audit=False):
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

        failures.append(
            etcd_pb2.RequestOp(
                request_range=etcd_pb2.RangeRequest(
                    key=path_encoded
                )
            )
        )

    channel = get_etcd_native_client()
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

    if not suppress_failure_audit:
        LOG.with_fields({'failed': failures}).info('Transaction failure')
    return False, failures
