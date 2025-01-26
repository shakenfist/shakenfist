from uuid import uuid4

from pydantic import ValidationError

from shakenfist.etcd_schema.operations.agent_op \
    import (chmod_command, execute_command, put_blob_command, model,
            current_version)
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class AgentOpTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

    def test_model(self):
        u1 = str(uuid4())
        u2 = str(uuid4())
        u3 = str(uuid4())

        commands = [
            put_blob_command(
                blob_uuid=u3,
                path='/tmp/foo'
            ),
            chmod_command(
                path='/tmp/foo',
                mode='ugo+r'
            ),
            execute_command(
                commandline='whoami',
                block=True
            )
        ]

        d = model(
            uuid=u1,
            namespace='my-namespace',
            instance_uuid=u2,
            commands=commands,
            version=current_version
        )

        serialized = d.model_dump(mode='json', by_alias=True)
        self.assertEqual(u1, serialized['uuid'])
        self.assertEqual('my-namespace', serialized['namespace'])
        self.assertEqual(u2, serialized['instance_uuid'])
        self.assertEqual([
            {
                'command': 'put-blob',
                'blob_uuid': u3,
                'path': '/tmp/foo'
            },
            {
                'command': 'chmod',
                'path': '/tmp/foo',
                'mode': 'ugo+r'
            },
            {
                'command': 'execute',
                'commandline': 'whoami',
                'block-for-result': True
            }
        ], serialized['commands'])
        self.assertEqual(current_version, serialized['version'])

    def test_model_bad_version(self):
        u1 = str(uuid4())
        u2 = str(uuid4())
        u3 = str(uuid4())

        commands = [
            put_blob_command(
                command='put-blob',
                blob_uuid=u3,
                path='/tmp/foo'
            ),
            chmod_command(
                command='chmod',
                path='/tmp/foo',
                mode='ugo+r'
            )
        ]

        self.assertRaises(
            ValidationError,
            model,
            uuid=u1,
            namespace='my-namespace',
            instance_uuid=u2,
            commands=commands,
            version=current_version + 1
        )
