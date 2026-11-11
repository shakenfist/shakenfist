# Copyright 2026 Michael Still and contributors
#
# Which operations an enqueue site waits for (issue 4673).
#
# Six enqueue sites used to serialise behind an object's newest mutating
# operation with runs_after=[obj.last_cluster_operation]. The newest
# operation is only a wait for the older ones if it was itself chained
# behind them, and several enqueue sites carry no runs_after at all -- the
# disk fetches of instance create, the redirected start in
# node_inst_netdesc_op, instance_create -- so an older pending operation
# was skipped and ran alongside the new work.
#
# Three of those sites now wait on every pending mutating operation via
# pending_cluster_operation_dependencies(). The other two mint their
# object in the same request and so wait for nothing at all. These tests
# pin which of the two each site is; the helper itself is covered against
# real SQL in test_object_cluster_operations.

import json
from unittest import mock
from uuid import uuid4

from shakenfist.artifact import Artifact
from shakenfist.baseobject import DatabaseBackedObjectWithOperations
from shakenfist import instance
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests.test_external_api import ExternalApiTestCase


# A dependency list no site could have produced from the pointer: the
# pointer is one operation, and these are two.
PENDING = [
    {'op_type': ObjectType.NODE_INST_OP, 'op_uuid': str(uuid4())},
    {'op_type': ObjectType.ARTIFACT_FETCH_OP, 'op_uuid': str(uuid4())},
]


class _PendingDependencies:
    """Patch the helper to a known answer, two operations long.

    Two is the point: a site which still read the pointer would produce a
    one-element runs_after, so the assertions below cannot pass by
    accident.
    """

    def setUp(self):
        super().setUp()
        self.pending = mock.patch.object(
            DatabaseBackedObjectWithOperations,
            'pending_cluster_operation_dependencies',
            return_value=list(PENDING))
        self.mock_pending = self.pending.start()
        self.addCleanup(self.pending.stop)

        # external_view() reads the pointer, so it is stubbed to keep
        # these tests off the target rows entirely.
        self.latest = mock.patch.object(
            DatabaseBackedObjectWithOperations, 'latest_cluster_operation',
            return_value={'op_type': ObjectType.NODE_INST_OP,
                          'op_uuid': str(uuid4())})
        self.latest.start()
        self.addCleanup(self.latest.stop)


class SnapshotWaitsForEveryPendingOperationTestCase(
        _PendingDependencies, ExternalApiTestCase):
    """Instance.snapshot() waits for the whole instance queue."""

    def test_success_criterion(self):
        inst = mock.MagicMock()
        inst.uuid = 'an-instance'
        inst.namespace = 'system'
        inst.uefi = False
        inst.block_devices = {'devices': [{
            'type': 'qcow2', 'device': 'vda',
            'path': '/somewhere/vda', 'snapshot_ignores': False}]}
        inst.pending_cluster_operation_dependencies.return_value = list(
            PENDING)

        with mock.patch('os.path.exists', return_value=True), \
                mock.patch('shakenfist.instance.niso_snapshot'), \
                mock.patch(
                    'shakenfist.instance.niso_create_and_enqueue') as enq:
            instance.Instance.snapshot(inst)

        self.assertEqual(PENDING, enq.call_args.kwargs['runs_after'])


class HotPlugWaitsForEveryPendingOperationTestCase(
        _PendingDependencies, ExternalApiTestCase):
    """POST /instances/<ref>/interfaces waits for the whole queue.

    The network reconcile operation the hot plug also enqueues targets
    the network rather than the instance, so it is not in the instance's
    pending list -- it stays in depends_on, which is the stronger edge.
    """

    def test_success_criterion(self):
        iface_uuid = str(uuid4())
        netdesc = {'network_uuid': str(uuid4()), 'iface_uuid': iface_uuid,
                   'address': '10.0.0.5', 'macaddress': '02:00:00:00:00:01',
                   'model': 'virtio', 'float': False}

        with mock.patch(
                'shakenfist.external_api.instance.instance.Instance.from_db',
                return_value=_hot_pluggable_instance()), \
            mock.patch('shakenfist.external_api.instance'
                       '._netdesc_safety_checks', return_value=None), \
            mock.patch('shakenfist.external_api.instance'
                       '._netdesc_allocate_address',
                       return_value=(netdesc, None)), \
            mock.patch('shakenfist.external_api.instance'
                       '.net_create_and_enqueue',
                       return_value=(ObjectType.NET_OP, str(uuid4()))), \
            mock.patch('shakenfist.external_api.instance'
                       '.NetworkInterface.from_db') as iface, \
            mock.patch('shakenfist.external_api.instance'
                       '.niio_create_and_enqueue') as enq:
            iface.return_value.external_view.return_value = {}
            resp = self.client.post(
                '/instances/%s/interfaces' % str(uuid4()),
                headers={'Authorization': self.auth_token},
                data=json.dumps({'network': {'network_uuid': 'anything'}}))

        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(PENDING, enq.call_args.kwargs['runs_after'])


class ArtifactFetchWaitsForEveryPendingFetchTestCase(
        _PendingDependencies, ExternalApiTestCase):
    """POST /artifacts waits for every pending fetch of the artifact.

    A fetch ends in add_index, which ends in delete_old_versions, so two
    running together race over the version list. An artifact's only
    mutating operation type is the fetch, so waiting on all of them
    serialises nothing unrelated.
    """

    def test_success_criterion(self):
        with mock.patch(
                'shakenfist.external_api.artifact.afo_create_and_enqueue'
        ) as enq:
            resp = self.client.post(
                '/artifacts',
                headers={'Authorization': self.auth_token},
                data=json.dumps({'url': 'http://example.com/an-image'}))

        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(PENDING, enq.call_args.kwargs['runs_after'])

    def test_an_existing_artifact_is_waited_on_too(self):
        # The pointer was read on an existing artifact as well as a new
        # one, and an existing artifact is the case with a queue.
        a = Artifact.new(Artifact.TYPE_IMAGE, 'http://example.com/old',
                         namespace='system')
        with mock.patch(
                'shakenfist.external_api.artifact.afo_create_and_enqueue'
        ) as enq:
            resp = self.client.post(
                '/artifacts',
                headers={'Authorization': self.auth_token},
                data=json.dumps({'url': 'http://example.com/old'}))

        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual(str(a.uuid), resp.get_json()['uuid'])
        self.assertEqual(PENDING, enq.call_args.kwargs['runs_after'])


class FreshlyMintedObjectsWaitForNothingTestCase(
        _PendingDependencies, ExternalApiTestCase):
    """The two sites whose object was minted in the same request.

    Their uuids are server generated and nothing else has seen them, so
    the pointer read they used to make always answered None (for the
    network) or named one of the disk fetches which depends_on already
    covers (for the instance start). Neither site reads the pointer now,
    and neither pays for the pending query either.
    """

    def test_network_create_passes_no_runs_after(self):
        with mock.patch(
                'shakenfist.network.network.net_create_and_enqueue') as enq:
            resp = self.client.post(
                '/networks',
                headers={'Authorization': self.auth_token},
                data=json.dumps({'netblock': '10.0.0.0/24',
                                 'name': 'a-network',
                                 'namespace': 'system'}))

        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertNotIn('runs_after', enq.call_args.kwargs)
        # The pointer is still read, but by external_view() building the
        # response -- not by the enqueue, which asks nothing at all.
        self.mock_pending.assert_not_called()

    def test_instance_create_passes_no_runs_after(self):
        with mock.patch('shakenfist.external_api.instance'
                        '.nino_create_and_enqueue') as enq:
            resp = self.client.post(
                '/instances',
                headers={'Authorization': self.auth_token},
                data=json.dumps({'name': 'an-instance',
                                 'cpus': 1,
                                 'memory': 1024,
                                 'network': [],
                                 'disk': [{'size': 8}],
                                 'namespace': 'system'}))

        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertNotIn('runs_after', enq.call_args.kwargs)
        # The pointer is still read, but by external_view() building the
        # response -- not by the enqueue, which asks nothing at all.
        self.mock_pending.assert_not_called()


def _hot_pluggable_instance():
    inst = mock.MagicMock()
    inst.uuid = str(uuid4())
    inst.namespace = 'system'
    inst.interfaces = []
    inst.placement = {'node': 'node1'}
    inst.pending_cluster_operation_dependencies.return_value = list(PENDING)
    return inst
