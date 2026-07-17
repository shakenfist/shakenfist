# Copyright 2019 Michael Still and contributors
"""Regression test for NodeInstSnapOp exception construction.

NodeInstSnapOpException captured op-level ``disk`` / ``artifact_uuid`` /
``blob_uuid`` / ``thin`` attributes that the op does not have -- they
live per-entry in the op's ``snapshots`` list. Raising AbortSnapshot et
al. therefore crashed with AttributeError, which the dispatcher logged
as an unhandled error and which tripped the post-test stable-log check.
The functional snapshot test passed anyway (the crash only corrupted the
error path), so this unit test guards the exception classes building
cleanly from a real op.
"""

from uuid import uuid4

from shakenfist.operations.node_inst_snap_op import AbortSnapshot
from shakenfist.operations.node_inst_snap_op import NodeInstSnapOp
from shakenfist.operations.node_inst_snap_op import NoSuchDisk
from shakenfist.schema.operations.node_inst_snap_op import create_and_enqueue
from shakenfist.schema.operations.node_inst_snap_op import model_tasks
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class NodeInstSnapOpExceptionTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def _make_op(self):
        snapshots = [{
            'disk': {'path': '/srv/shakenfist/instances/x/vda', 'type': 'qcow2'},
            'artifact_uuid': str(uuid4()),
            'blob_uuid': str(uuid4()),
            'thin': True,
        }]
        _, op_uuid = create_and_enqueue(
            node_uuid=str(uuid4()),
            instance_uuid=str(uuid4()),
            snapshots=snapshots,
            tasks=[model_tasks.instance_snapshot],
            priority=PRIORITY.user_waiting,
        )
        op = NodeInstSnapOp.from_db(op_uuid)
        self.assertIsNotNone(op)
        return op

    def test_exception_builds_from_op_without_attribute_error(self):
        op = self._make_op()

        # The bug was that constructing the exception referenced op.disk /
        # op.artifact_uuid / op.blob_uuid / op.thin, none of which are
        # op-level attributes -- so this would raise AttributeError. Build
        # it and confirm it captured the op's real attributes instead.
        exc = AbortSnapshot(op, 'boom')
        self.assertEqual(op.uuid, exc.op_uuid)
        self.assertEqual(op.instance_uuid, exc.instance_uuid)
        self.assertEqual(op.snapshots, exc.snapshots)

        # A no-message subclass must build cleanly too.
        NoSuchDisk(op)
