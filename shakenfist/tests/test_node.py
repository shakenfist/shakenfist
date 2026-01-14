from unittest import mock

from shakenfist.node import Node
from shakenfist.schema.object_reference import ObjectReference
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.tests import base


class NodeBlobsTestCase(base.ShakenFistTestCase):
    """Tests for the Node.blobs property."""

    @mock.patch('shakenfist.node.mariadb.get_references_from')
    @mock.patch.object(Node, '__init__', lambda self, static_values: None)
    def test_blobs_returns_blob_uuids(self, mock_get_refs):
        """Test that Node.blobs returns a list of blob UUIDs."""
        blob_uuid1 = '11111111-1111-1111-1111-111111111111'
        blob_uuid2 = '22222222-2222-2222-2222-222222222222'

        mock_get_refs.return_value = [
            ObjectReference(
                source_object_type=ObjectType.NODE,
                source_uuid='node1.example.com',
                relationship=RelationshipType.BLOB_LOCATION,
                relationship_value=None,
                target_object_type=ObjectType.BLOB,
                target_uuid=blob_uuid1,
                created=1234567890.0,
                last_active=1234567890.0
            ),
            ObjectReference(
                source_object_type=ObjectType.NODE,
                source_uuid='node1.example.com',
                relationship=RelationshipType.BLOB_LOCATION,
                relationship_value=None,
                target_object_type=ObjectType.BLOB,
                target_uuid=blob_uuid2,
                created=1234567890.0,
                last_active=1234567890.0
            ),
        ]

        node = Node.__new__(Node)
        node._Node__node_fqdn = 'node1.example.com'

        blobs = node.blobs

        self.assertEqual(blobs, [blob_uuid1, blob_uuid2])
        mock_get_refs.assert_called_once_with(
            ObjectType.NODE, 'node1.example.com', RelationshipType.BLOB_LOCATION)

    @mock.patch('shakenfist.node.mariadb.get_references_from')
    @mock.patch.object(Node, '__init__', lambda self, static_values: None)
    def test_blobs_returns_empty_list_when_no_blobs(self, mock_get_refs):
        """Test that Node.blobs returns empty list when node has no blobs."""
        mock_get_refs.return_value = []

        node = Node.__new__(Node)
        node._Node__node_fqdn = 'node1.example.com'

        blobs = node.blobs

        self.assertEqual(blobs, [])
        mock_get_refs.assert_called_once_with(
            ObjectType.NODE, 'node1.example.com', RelationshipType.BLOB_LOCATION)
