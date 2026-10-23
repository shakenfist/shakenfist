# Copyright 2026 Michael Still and contributors

"""Each batchable class's hard_delete() against the batch's table list.

mariadb.HARD_DELETE_BATCH_FUNCTIONS declares, for each object type whose
hard delete is nothing but row deletes keyed on the object, the public
delete functions that type's hard delete runs. These tests run each such
class's real hard_delete() with every function on the mariadb module,
the event log, the lock primitives and the file removal calls replaced
by recorders. With the batch, a class must make one unconditional
hard_delete_objects() call for itself and write one audit event, and
nothing else. Without it, the calls must equal a literal sequence per
type written here: the deletes the class made before the batch existed.
A delete added to or dropped from the declaration, a row delete left in
a class beside the batch, or any other database call, lock, event or
file removal added to a hard_delete(), fails here.

The literals are deliberately a second copy. A change to what a type's
hard delete removes has to be made here as well as in mariadb.py.
"""
from contextlib import ExitStack
import importlib
import inspect
import os
import shutil
import time
from unittest import mock
from uuid import UUID
from uuid import uuid4

from shakenfist.artifact import Artifact
from shakenfist.baseobject import DatabaseBackedObject
from shakenfist.blob import Blob
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import get_object_class
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist.instance import Instance
from shakenfist import ipam
from shakenfist.ipam import IPAM
from shakenfist import locks
from shakenfist.managed_executables.dnsmasq import DnsMasq
from shakenfist.mapping_rule import MappingRule
from shakenfist import mariadb
from shakenfist.namespace_key import NamespaceKey
from shakenfist.network.interface import NetworkInterface
from shakenfist.network.network import Network
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.schema.blob_data import BlobData
from shakenfist.schema.dnsmasq import DnsMasqData
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.upload import UploadData
from shakenfist.tests import base
from shakenfist.trusted_issuer import TrustedIssuer
from shakenfist.upload import Upload
from shakenfist.util import concurrency as util_concurrency


# What every type's hard_delete() runs last, from
# DatabaseBackedObject.hard_delete().
BASE_DELETES = (
    'delete_state',
    'delete_object_metadata',
    'delete_object_events',
)

# BaseClusterOperation.hard_delete(), which every cluster operation
# inherits unchanged.
CLUSTER_OPERATION_DELETES = (
    'delete_cluster_operation_target',
    'delete_cluster_operation_error',
    'delete_cluster_operation',
)

# For each batchable type, the deletes its hard delete runs before
# BASE_DELETES, in order, as read from each class before it moved onto the
# batch.
EXPECTED_DELETES = {
    ObjectType.ARTIFACT_FETCH_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.IMGCACHE_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NET_IFACE_IP_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NET_IFACE_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NET_IP_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NET_MACADDR_IP_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NET_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_AOP_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_BLOB_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_INST_NET_IFACE_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_INST_NETDESC_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_INST_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_INST_SNAP_OP: CLUSTER_OPERATION_DELETES,
    ObjectType.NODE_NET_OP: CLUSTER_OPERATION_DELETES,
    # AgentOperation derives from BaseOperation, not BaseClusterOperation,
    # so it has none of the cluster operation deletes.
    ObjectType.AGENTOPERATION: (
        'delete_agent_operation_attributes',
        'delete_agent_operation',
        'remove_all_references_from',
    ),
    # Artifact's own deletes, then the targets delete from
    # DatabaseBackedObjectWithOperations.
    ObjectType.ARTIFACT: (
        'delete_all_artifact_indexes',
        'delete_artifact_attributes',
        'delete_artifact',
        'delete_cluster_operation_targets_for_object',
    ),
    # Network's own deletes, then the targets delete from
    # DatabaseBackedObjectWithOperations.
    ObjectType.NETWORK: (
        'delete_network_attributes',
        'delete_network',
        'delete_cluster_operation_targets_for_object',
    ),
    ObjectType.BLOB: (
        'delete_blob_hashes',
        'delete_blob_transfers_for_blob',
        'delete_blob_attributes',
        'delete_blob',
    ),
    ObjectType.DHCP: (
        'delete_dnsmasq',
    ),
    ObjectType.INTERFACE: (
        'delete_network_interface_attributes',
        'delete_network_interface',
    ),
    # An IPAM which is not in memory only.
    ObjectType.IPAM: (
        'delete_reservations_for_ipam',
        'delete_ipam',
    ),
    ObjectType.NAMESPACE_KEY: (
        'delete_namespace_key_attributes',
        'delete_namespace_key',
    ),
    ObjectType.TRUSTED_ISSUER: (
        'delete_trusted_issuer_attributes',
        'delete_trusted_issuer',
    ),
    ObjectType.MAPPING_RULE: (
        'delete_mapping_rule_attributes',
        'delete_mapping_rule',
    ),
    ObjectType.UPLOAD: (
        'delete_upload',
    ),
}

# The types whose hard_delete() does more than delete rows keyed on the
# object, and so is not batched.
PER_OBJECT_TYPES = {
    ObjectType.INSTANCE,
    ObjectType.NAMESPACE,
    ObjectType.NAMESPACE_CLAIM,
    ObjectType.NODE,
}

AUDIT_EVENT = 'eventlog.add_event'
BATCH = 'hard_delete_objects'


def _normalise(value):
    # The classes pass a uuid as a UUID or a str, as the function they
    # call accepts; what matters here is which object is named.
    if isinstance(value, UUID):
        return str(value)
    return value


class _Recorder:
    """Replaces everything a hard_delete() could reach with recorders.

    Every function on the mariadb module, so that any database call is
    seen and none reaches a database; the event log; the cluster and
    node locks; and the file removal calls. Each call is recorded as
    (name, args, kwargs) with uuids as strings.
    """

    def __init__(self, replies=None):
        # replies maps a name to a callable which answers its calls in
        # place of the default MagicMock, and may raise.
        self.calls = []
        self.replies = replies or {}

    def _stub(self, name):
        def record(*args, **kwargs):
            self.calls.append((
                name,
                tuple(_normalise(a) for a in args),
                {k: _normalise(v) for k, v in kwargs.items()}))
            if name in self.replies:
                return self.replies[name](*args, **kwargs)
            return mock.MagicMock()
        return record

    def install(self, stack, keep=()):
        """Patch everything, except the mariadb functions named in keep."""
        for name, value in vars(mariadb).items():
            if inspect.isfunction(value) and name not in keep:
                stack.enter_context(
                    mock.patch.object(mariadb, name, self._stub(name)))

        targets = [
            (eventlog, 'add_event', AUDIT_EVENT),
            (eventlog, 'add_event_multi', 'eventlog.add_event_multi'),
            (locks, 'ClusterLock', 'locks.ClusterLock'),
            (util_concurrency, 'NodeLock', 'util_concurrency.NodeLock'),
            (DatabaseBackedObject, 'get_lock', 'get_lock'),
            (DatabaseBackedObject, 'get_lock_attr', 'get_lock_attr'),
            (os, 'remove', 'os.remove'),
            (os, 'unlink', 'os.unlink'),
            (shutil, 'rmtree', 'shutil.rmtree'),
        ]
        for target, attribute, name in targets:
            stack.enter_context(
                mock.patch.object(target, attribute, self._stub(name)))

    def names(self):
        return [name for name, _, _ in self.calls]

    def database_calls(self):
        return [call for call in self.calls
                if hasattr(mariadb, call[0])]


def _cluster_operation(object_type, object_uuid):
    """A cluster operation built by its class from minimal static values."""
    schema = importlib.import_module(
        f'shakenfist.schema.operations.{object_type}')
    cls = get_object_class(str(object_type))
    static_values = {field: None for field in schema.model.model_fields}
    for field in static_values:
        if field.endswith('_uuid'):
            static_values[field] = str(uuid4())
    static_values.update({
        'uuid': object_uuid,
        'priority': 'background',
        'tasks': [],
        'version': cls.current_version,
    })
    return cls(static_values)


def _ipam(object_uuid):
    return IPAM({
        'uuid': object_uuid,
        'namespace': 'test',
        'network_uuid': str(uuid4()),
        'ipblock': '10.0.0.0/24',
        'version': IPAM.current_version,
        'in_memory_only': False,
    })


def _network(object_uuid):
    # Network's constructor loads its IPAM, which is not under test.
    with mock.patch.object(ipam.IPAM, 'from_db',
                           return_value=_ipam(object_uuid)):
        return Network({
            'uuid': object_uuid,
            'namespace': 'test',
            'name': 'test',
            'netblock': '10.0.0.0/24',
            'version': Network.current_version,
        })


BUILDERS = {
    ObjectType.AGENTOPERATION: lambda u: AgentOperation({
        'uuid': u,
        'namespace': 'test',
        'instance_uuid': str(uuid4()),
        'commands': [],
        'version': AgentOperation.current_version,
    }),
    ObjectType.ARTIFACT: lambda u: Artifact({
        'uuid': u,
        'artifact_type': 'image',
        'source_url': 'http://example.com/image',
        'name': 'test',
        'namespace': 'test',
        'version': Artifact.current_version,
    }),
    ObjectType.BLOB: lambda u: Blob(BlobData(
        uuid=u, modified=time.time(), fetched_at=time.time(),
        version=Blob.current_version)),
    ObjectType.DHCP: lambda u: DnsMasq(DnsMasqData(
        uuid=u, namespace='test', owner_type=ObjectType.NETWORK,
        owner_uuid=uuid4(), version=DnsMasq.current_version,
        provide_dhcp=True, provide_dns=False)),
    ObjectType.INTERFACE: lambda u: NetworkInterface({
        'uuid': u,
        'network_uuid': str(uuid4()),
        'instance_uuid': str(uuid4()),
        'macaddr': '02:00:00:00:00:01',
        'ipv4': '10.0.0.2',
        'order': 0,
        'model': 'virtio',
        'version': NetworkInterface.current_version,
    }),
    ObjectType.IPAM: _ipam,
    ObjectType.MAPPING_RULE: lambda u: MappingRule({
        'uuid': u,
        'namespace': 'test',
        'name': 'test',
        'version': MappingRule.current_version,
    }),
    ObjectType.NAMESPACE_KEY: lambda u: NamespaceKey({
        'uuid': u,
        'namespace': 'test',
        'name': 'test',
        'version': NamespaceKey.current_version,
    }),
    ObjectType.NETWORK: _network,
    ObjectType.TRUSTED_ISSUER: lambda u: TrustedIssuer({
        'uuid': u,
        'name': 'test',
        'version': TrustedIssuer.current_version,
    }),
    ObjectType.UPLOAD: lambda u: Upload(UploadData(
        uuid=u, node='node1', created_at=time.time(),
        version=Upload.current_version)),
}
for _name in OPERATION_NAMES_TO_CLASSES:
    BUILDERS[ObjectType(_name)] = (
        lambda u, t=ObjectType(_name): _cluster_operation(t, u))


class ClassificationTestCase(base.ShakenFistTestCase):
    def test_every_type_is_batched_or_per_object(self):
        batched = set(mariadb.HARD_DELETE_BATCH_FUNCTIONS)
        for name in OBJECT_NAMES_TO_CLASSES:
            object_type = ObjectType(name)
            self.assertNotEqual(
                object_type in batched, object_type in PER_OBJECT_TYPES,
                f'{name} must be batched or per object, and not both')

    def test_per_object_types_exist(self):
        for object_type in PER_OBJECT_TYPES:
            self.assertIn(str(object_type), OBJECT_NAMES_TO_CLASSES)

    def test_every_batched_type_has_a_literal(self):
        self.assertEqual(set(EXPECTED_DELETES),
                         set(mariadb.HARD_DELETE_BATCH_FUNCTIONS))

    def test_every_literal_type_has_a_builder(self):
        self.assertEqual(set(EXPECTED_DELETES), set(BUILDERS))


def _batch_deletes_all(object_type, object_uuids, **kwargs):
    return list(object_uuids)


def _batch_unavailable(*args, **kwargs):
    raise exceptions.HardDeleteBatchUnavailable('too old')


class HardDeleteParityTestCase(base.ShakenFistTestCase):
    def _build(self, object_type):
        """Construct the type's object with no database behind it."""
        object_uuid = str(uuid4())
        with ExitStack() as stack:
            _Recorder().install(stack)
            obj = BUILDERS[object_type](object_uuid)
        self.assertIsInstance(
            obj, get_object_class(str(object_type)), object_type)
        self.assertEqual(object_uuid, str(obj.uuid))
        self.assertFalse(obj.in_memory_only, object_type)
        return obj

    def _class_hard_delete(self, object_type, batch, keep=()):
        """Run the class's hard_delete() with ``batch`` answering the batch."""
        obj = self._build(object_type)
        recorder = _Recorder(replies={BATCH: batch})
        with ExitStack() as stack:
            recorder.install(stack, keep=keep)
            obj.hard_delete()
        return str(obj.uuid), recorder

    def _assert_audit_event(self, object_type, object_uuid, call):
        self.assertEqual(
            (AUDIT_EVENT,
             (EVENT_TYPE_AUDIT, object_type, object_uuid,
              'hard deleted object')),
            call[:2], object_type)

    def test_class_makes_one_batch_call(self):
        # The complete record: one unconditional batch for this object,
        # then one audit event. A row delete left in the class, the
        # targets delete run twice, or any other call breaks the equality.
        for object_type in EXPECTED_DELETES:
            object_uuid, recorder = self._class_hard_delete(
                object_type, _batch_deletes_all)
            self.assertEqual([BATCH, AUDIT_EVENT], recorder.names(),
                             object_type)
            batch, event = recorder.calls
            self.assertEqual(
                (BATCH, (object_type, [object_uuid]),
                 {'unconditional': True}),
                batch, object_type)
            self._assert_audit_event(object_type, object_uuid, event)

    def test_without_the_batch_calls_the_literal(self):
        # Against a database service which predates the batch, the class
        # falls back to hard_delete_objects_per_call(), which must make
        # exactly the calls the class made before the batch.
        for object_type, expected in EXPECTED_DELETES.items():
            object_uuid, recorder = self._class_hard_delete(
                object_type, _batch_unavailable,
                keep=('hard_delete_objects_per_call',))
            self.assertEqual(
                [BATCH] + list(expected) + list(BASE_DELETES) + [AUDIT_EVENT],
                recorder.names(), object_type)

            _, *deletes, event = recorder.calls
            for name, args, kwargs in deletes:
                self.assertIn(object_uuid, args, (object_type, name))
                self.assertEqual({}, kwargs, (object_type, name))
            self._assert_audit_event(object_type, object_uuid, event)

    def test_failed_batch_writes_no_audit_event(self):
        # None is a batch which rolled back. The object is still there,
        # so neither an audit event nor a per-call retry follows.
        for object_type in EXPECTED_DELETES:
            _, recorder = self._class_hard_delete(
                object_type, lambda *args, **kwargs: None)
            self.assertEqual([BATCH], recorder.names(), object_type)

    def test_batch_omitting_the_object_writes_no_audit_event(self):
        # An unconditional batch returns every uuid it was given. One
        # which does not has broken that contract, and is not trusted.
        for object_type in EXPECTED_DELETES:
            _, recorder = self._class_hard_delete(
                object_type, lambda *args, **kwargs: [str(uuid4())])
            self.assertEqual([BATCH], recorder.names(), object_type)

    def test_literal_matches_the_mapping(self):
        for object_type, expected in EXPECTED_DELETES.items():
            self.assertEqual(
                expected,
                tuple(mariadb.HARD_DELETE_BATCH_FUNCTIONS.get(object_type, ())),
                object_type)

    def test_instance_keeps_its_targets_delete(self):
        # Instance is not batched, so the cluster operation targets delete
        # it inherits still runs, once, between its own deletes and the
        # three every type runs. Its placement release is not under test.
        object_uuid = str(uuid4())
        # Built without its constructor, which would need a database;
        # hard_delete() reads only the uuid and whether it is in memory.
        inst = Instance.__new__(Instance)
        inst._DatabaseBackedObject__uuid = object_uuid
        inst._DatabaseBackedObject__in_memory_only = False
        recorder = _Recorder()
        with ExitStack() as stack:
            recorder.install(stack)
            stack.enter_context(
                mock.patch.object(Instance, '_release_placement'))
            inst.hard_delete()
        self.assertEqual(
            ['delete_instance_attributes', 'delete_instance',
             'delete_cluster_operation_targets_for_object']
            + list(BASE_DELETES) + [AUDIT_EVENT],
            recorder.names())
        self.assertEqual(
            ('delete_cluster_operation_targets_for_object',
             (ObjectType.INSTANCE, object_uuid), {}),
            recorder.calls[2])

    def test_in_memory_ipam_deletes_nothing(self):
        with ExitStack() as stack:
            _Recorder().install(stack)
            obj = IPAM.new(str(uuid4()), 'test', str(uuid4()), '10.0.0.0/24',
                           in_memory_only=True)
        self.assertTrue(obj.in_memory_only)
        recorder = _Recorder()
        with ExitStack() as stack:
            recorder.install(stack)
            obj.hard_delete()
        self.assertEqual([], recorder.calls)
