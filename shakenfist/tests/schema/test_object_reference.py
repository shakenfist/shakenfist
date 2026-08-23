# Tests for schema/object_reference.py and schema/relationship_types.py.
#
# This module tests:
# - RelationshipType enum values and proto_id mapping
# - ObjectReference Pydantic model validation
# - SQL table generation from ObjectReference model

from unittest import mock
from uuid import uuid4

import sqlalchemy as sa

from shakenfist.schema.object_reference import ObjectReference
from shakenfist.schema.object_reference import references_to_grouped_dict
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
from shakenfist.tests import base


# Generate valid identifier values for tests (strings since the schema uses
# strings to accommodate both UUIDs and Node FQDNs)
SOURCE_UUID = str(uuid4())
TARGET_UUID = str(uuid4())


class RelationshipTypeEnumTestCase(base.ShakenFistTestCase):
    """Tests for the RelationshipType enum."""

    def test_enum_values(self):
        """Test that all expected relationship types exist."""
        expected_types = [
            'disk', 'nvram_template', 'artifact_index',
            'depends_on', 'transcode', 'agent_output', 'blob_location'
        ]
        for type_name in expected_types:
            with self.subTest(type_name=type_name):
                self.assertIn(type_name, [e.value for e in RelationshipType])

    def test_str_inheritance(self):
        """Test that RelationshipType inherits from str properly."""
        self.assertEqual(RelationshipType.DISK.value, 'disk')
        self.assertEqual(str(RelationshipType.DISK), 'disk')

    def test_proto_id_values(self):
        """Test that all relationship types have unique proto_id values."""
        proto_ids = [e.proto_id for e in RelationshipType]
        self.assertEqual(len(proto_ids), len(set(proto_ids)),
                         'proto_id values must be unique')

    def test_proto_id_nonzero(self):
        """Test that no proto_id is zero (reserved for UNSPECIFIED)."""
        for rel_type in RelationshipType:
            with self.subTest(rel_type=rel_type):
                self.assertNotEqual(rel_type.proto_id, 0,
                                    f'{rel_type} has proto_id=0')

    def test_from_proto_id(self):
        """Test from_proto_id lookup works correctly."""
        for rel_type in RelationshipType:
            with self.subTest(rel_type=rel_type):
                looked_up = RelationshipType.from_proto_id(rel_type.proto_id)
                self.assertEqual(looked_up, rel_type)

    def test_from_proto_id_zero(self):
        """Test that proto_id 0 returns None (UNSPECIFIED)."""
        self.assertIsNone(RelationshipType.from_proto_id(0))

    def test_from_proto_id_unknown(self):
        """Test that unknown proto_id returns None."""
        self.assertIsNone(RelationshipType.from_proto_id(9999))

    def test_specific_proto_ids(self):
        """Test that specific relationship types have expected proto_ids."""
        self.assertEqual(RelationshipType.DISK.proto_id, 1)
        self.assertEqual(RelationshipType.NVRAM_TEMPLATE.proto_id, 2)
        self.assertEqual(RelationshipType.ARTIFACT_INDEX.proto_id, 3)
        self.assertEqual(RelationshipType.DEPENDS_ON.proto_id, 4)
        self.assertEqual(RelationshipType.TRANSCODE.proto_id, 5)
        self.assertEqual(RelationshipType.AGENT_OUTPUT.proto_id, 6)
        self.assertEqual(RelationshipType.BLOB_LOCATION.proto_id, 7)


class ObjectReferenceModelTestCase(base.ShakenFistTestCase):
    """Tests for the ObjectReference Pydantic model."""

    def test_basic_creation(self):
        """Test creating a valid ObjectReference."""
        ref = ObjectReference(
            source_object_type=ObjectType.INSTANCE,
            source_uuid=SOURCE_UUID,
            relationship=RelationshipType.DISK,
            relationship_value='0',
            target_object_type=ObjectType.BLOB,
            target_uuid=TARGET_UUID,
            created=1234567890.0,
            last_active=1234567890.0
        )
        self.assertEqual(ref.source_object_type, ObjectType.INSTANCE)
        self.assertEqual(ref.relationship, RelationshipType.DISK)
        self.assertEqual(ref.relationship_value, '0')
        self.assertEqual(ref.target_object_type, ObjectType.BLOB)

    def test_optional_relationship_value(self):
        """Test that relationship_value can be None."""
        ref = ObjectReference(
            source_object_type=ObjectType.BLOB,
            source_uuid=SOURCE_UUID,
            relationship=RelationshipType.DEPENDS_ON,
            relationship_value=None,
            target_object_type=ObjectType.BLOB,
            target_uuid=TARGET_UUID,
            created=1234567890.0,
            last_active=1234567890.0
        )
        self.assertIsNone(ref.relationship_value)

    def test_relationship_value_max_length(self):
        """Test that relationship_value has max length of 64."""
        # Should work with 64 chars
        ref = ObjectReference(
            source_object_type=ObjectType.INSTANCE,
            source_uuid=SOURCE_UUID,
            relationship=RelationshipType.DISK,
            relationship_value='a' * 64,
            target_object_type=ObjectType.BLOB,
            target_uuid=TARGET_UUID,
            created=1234567890.0,
            last_active=1234567890.0
        )
        self.assertEqual(len(ref.relationship_value), 64)

    def test_timestamps(self):
        """Test that timestamps are stored correctly."""
        created = 1704067200.0  # 2024-01-01 00:00:00
        last_active = 1704153600.0  # 2024-01-02 00:00:00
        ref = ObjectReference(
            source_object_type=ObjectType.INSTANCE,
            source_uuid=SOURCE_UUID,
            relationship=RelationshipType.DISK,
            relationship_value='0',
            target_object_type=ObjectType.BLOB,
            target_uuid=TARGET_UUID,
            created=created,
            last_active=last_active
        )
        self.assertEqual(ref.created, created)
        self.assertEqual(ref.last_active, last_active)

    def test_node_fqdn_as_source(self):
        """Test that Node FQDNs work as source identifiers.

        Node references in the object_references table use the FQDN
        (e.g., 'localhost', 'node1.example.com') as their identifier.
        This is one of the use cases that requires source_uuid to be a
        string rather than UUID (Namespace is the other).
        """
        node_fqdn = 'node1.example.com'
        ref = ObjectReference(
            source_object_type=ObjectType.NODE,
            source_uuid=node_fqdn,
            relationship=RelationshipType.BLOB_LOCATION,
            relationship_value=None,
            target_object_type=ObjectType.BLOB,
            target_uuid=TARGET_UUID,
            created=1234567890.0,
            last_active=1234567890.0
        )
        self.assertEqual(ref.source_uuid, node_fqdn)
        self.assertEqual(ref.source_object_type, ObjectType.NODE)

    def test_long_fqdn_as_source(self):
        """Test that long FQDNs (up to 255 chars) work as identifiers."""
        # DNS names can be up to 253 characters
        long_fqdn = 'a' * 200 + '.example.com'
        ref = ObjectReference(
            source_object_type=ObjectType.NODE,
            source_uuid=long_fqdn,
            relationship=RelationshipType.BLOB_LOCATION,
            relationship_value=None,
            target_object_type=ObjectType.BLOB,
            target_uuid=TARGET_UUID,
            created=1234567890.0,
            last_active=1234567890.0
        )
        self.assertEqual(ref.source_uuid, long_fqdn)


class ObjectReferenceTableGenerationTestCase(base.ShakenFistTestCase):
    """Tests for generating the object_references table."""

    def test_table_schema(self):
        """Test that ObjectReference generates correct table schema."""
        metadata = sa.MetaData()
        table = pydantic_to_sqlalchemy_table(
            ObjectReference, 'object_references', metadata,
            primary_key_fields=[
                'source_object_type', 'source_uuid', 'relationship',
                'relationship_value', 'target_object_type', 'target_uuid'
            ],
            include_id_column=False)

        # Check all columns exist
        expected_cols = [
            'source_object_type', 'source_uuid', 'relationship',
            'relationship_value', 'target_object_type', 'target_uuid',
            'created', 'last_active'
        ]
        for col_name in expected_cols:
            with self.subTest(col_name=col_name):
                self.assertIn(col_name, table.c)

    def test_primary_key_columns(self):
        """Test that primary key columns are correct."""
        metadata = sa.MetaData()
        table = pydantic_to_sqlalchemy_table(
            ObjectReference, 'object_references', metadata,
            primary_key_fields=[
                'source_object_type', 'source_uuid', 'relationship',
                'relationship_value', 'target_object_type', 'target_uuid'
            ],
            include_id_column=False)

        pk_cols = [c.name for c in table.primary_key.columns]
        self.assertIn('source_object_type', pk_cols)
        self.assertIn('source_uuid', pk_cols)
        self.assertIn('relationship', pk_cols)
        self.assertIn('target_object_type', pk_cols)
        self.assertIn('target_uuid', pk_cols)

    def test_uuid_columns_are_strings(self):
        """Test that identifier columns use String type.

        The columns are named 'uuid' for historical reasons, but they store
        strings (VARCHAR 255) to accommodate both UUIDs and Node FQDNs.
        """
        metadata = sa.MetaData()
        table = pydantic_to_sqlalchemy_table(
            ObjectReference, 'object_references', metadata,
            primary_key_fields=[
                'source_object_type', 'source_uuid', 'relationship',
                'relationship_value', 'target_object_type', 'target_uuid'
            ],
            include_id_column=False)

        # Both identifier columns should use String type
        self.assertIsInstance(table.c.source_uuid.type, sa.String)
        self.assertIsInstance(table.c.target_uuid.type, sa.String)
        # Check the max length is 255 (for FQDNs)
        self.assertEqual(table.c.source_uuid.type.length, 255)
        self.assertEqual(table.c.target_uuid.type.length, 255)

    def test_indexes_exist(self):
        """Test that expected indexes are created."""
        metadata = sa.MetaData()
        table = pydantic_to_sqlalchemy_table(
            ObjectReference, 'object_references', metadata,
            primary_key_fields=[
                'source_object_type', 'source_uuid', 'relationship',
                'relationship_value', 'target_object_type', 'target_uuid'
            ],
            include_id_column=False)

        index_names = [idx.name for idx in table.indexes]
        # Check for compound indexes from model_config
        self.assertIn('idx_object_references_source_object_type_source_uuid',
                      index_names)
        self.assertIn('idx_object_references_target_object_type_target_uuid',
                      index_names)
        self.assertIn('idx_object_references_relationship_relationship_value',
                      index_names)
        self.assertIn('idx_object_references_created', index_names)
        self.assertIn('idx_object_references_last_active', index_names)


class ObjectReferenceSerializationTestCase(base.ShakenFistTestCase):
    """Tests for ObjectReference serialization methods."""

    def test_external_view_basic(self):
        """Test that external_view() returns correct dictionary."""
        ref = ObjectReference(
            source_object_type=ObjectType.INSTANCE,
            source_uuid=SOURCE_UUID,
            relationship=RelationshipType.DISK,
            relationship_value='0',
            target_object_type=ObjectType.BLOB,
            target_uuid=TARGET_UUID,
            created=1234567890.0,
            last_active=1234567891.0
        )
        result = ref.external_view()

        self.assertIsInstance(result, dict)
        self.assertEqual(result['source_object_type'], 'instance')
        self.assertEqual(result['source_uuid'], SOURCE_UUID)
        self.assertEqual(result['relationship'], 'disk')
        self.assertEqual(result['relationship_value'], '0')
        self.assertEqual(result['target_object_type'], 'blob')
        self.assertEqual(result['target_uuid'], TARGET_UUID)
        self.assertEqual(result['created'], 1234567890.0)
        self.assertEqual(result['last_active'], 1234567891.0)

    def test_external_view_null_relationship_value(self):
        """Test that external_view() handles null relationship_value."""
        ref = ObjectReference(
            source_object_type=ObjectType.NODE,
            source_uuid='sf-1',
            relationship=RelationshipType.BLOB_LOCATION,
            relationship_value=None,
            target_object_type=ObjectType.BLOB,
            target_uuid=TARGET_UUID,
            created=1234567890.0,
            last_active=1234567890.0
        )
        result = ref.external_view()

        self.assertIsNone(result['relationship_value'])

    def test_external_view_enum_string_values(self):
        """Test that enums serialize as string values, not proto_ids."""
        ref = ObjectReference(
            source_object_type=ObjectType.ARTIFACT,
            source_uuid=SOURCE_UUID,
            relationship=RelationshipType.ARTIFACT_INDEX,
            relationship_value='000000000001',
            target_object_type=ObjectType.BLOB,
            target_uuid=TARGET_UUID,
            created=1234567890.0,
            last_active=1234567890.0
        )
        result = ref.external_view()

        # Should be string values, not integers
        self.assertEqual(result['source_object_type'], 'artifact')
        self.assertEqual(result['relationship'], 'artifact_index')
        self.assertEqual(result['target_object_type'], 'blob')

    def test_references_to_grouped_dict_empty_list(self):
        """Test references_to_grouped_dict with empty list."""
        with mock.patch(
                'shakenfist.schema.object_reference.request_namespace',
                return_value='system'):
            result = references_to_grouped_dict([])
            self.assertEqual(result, {})

    def test_references_to_grouped_dict_single_reference(self):
        """Test references_to_grouped_dict with a single reference."""
        with mock.patch(
                'shakenfist.schema.object_reference.request_namespace',
                return_value='system'):
            ref = ObjectReference(
                source_object_type=ObjectType.INSTANCE,
                source_uuid=SOURCE_UUID,
                relationship=RelationshipType.DISK,
                relationship_value='0',
                target_object_type=ObjectType.BLOB,
                target_uuid=TARGET_UUID,
                created=1234567890.0,
                last_active=1234567890.0
            )
            result = references_to_grouped_dict([ref])

            self.assertIn('disk', result)
            self.assertEqual(len(result['disk']), 1)
            self.assertEqual(result['disk'][0]['relationship'], 'disk')

    def test_references_to_grouped_dict_multiple_same_type(self):
        """Test grouping multiple references of same type."""
        with mock.patch(
                'shakenfist.schema.object_reference.request_namespace',
                return_value='system'):
            ref1 = ObjectReference(
                source_object_type=ObjectType.INSTANCE,
                source_uuid=SOURCE_UUID,
                relationship=RelationshipType.DISK,
                relationship_value='0',
                target_object_type=ObjectType.BLOB,
                target_uuid=TARGET_UUID,
                created=1234567890.0,
                last_active=1234567890.0
            )
            ref2 = ObjectReference(
                source_object_type=ObjectType.INSTANCE,
                source_uuid=SOURCE_UUID,
                relationship=RelationshipType.DISK,
                relationship_value='1',
                target_object_type=ObjectType.BLOB,
                target_uuid=str(uuid4()),
                created=1234567891.0,
                last_active=1234567891.0
            )
            result = references_to_grouped_dict([ref1, ref2])

            self.assertEqual(len(result), 1)  # Only one relationship type
            self.assertIn('disk', result)
            self.assertEqual(len(result['disk']), 2)  # Two references

    def test_references_to_grouped_dict_multiple_different_types(self):
        """Test grouping references of different types."""
        with mock.patch(
                'shakenfist.schema.object_reference.request_namespace',
                return_value='system'):
            ref1 = ObjectReference(
                source_object_type=ObjectType.INSTANCE,
                source_uuid=SOURCE_UUID,
                relationship=RelationshipType.DISK,
                relationship_value='0',
                target_object_type=ObjectType.BLOB,
                target_uuid=TARGET_UUID,
                created=1234567890.0,
                last_active=1234567890.0
            )
            ref2 = ObjectReference(
                source_object_type=ObjectType.INSTANCE,
                source_uuid=SOURCE_UUID,
                relationship=RelationshipType.NVRAM_TEMPLATE,
                relationship_value=None,
                target_object_type=ObjectType.BLOB,
                target_uuid=str(uuid4()),
                created=1234567890.0,
                last_active=1234567890.0
            )
            result = references_to_grouped_dict([ref1, ref2])

            self.assertEqual(len(result), 2)  # Two relationship types
            self.assertIn('disk', result)
            self.assertIn('nvram_template', result)
            self.assertEqual(len(result['disk']), 1)
            self.assertEqual(len(result['nvram_template']), 1)

    def test_references_to_grouped_dict_namespaced_requester(self):
        """Test that a namespaced requester sees non-Node references."""
        with mock.patch(
                'shakenfist.schema.object_reference.request_namespace',
                return_value='testns'):
            ref1 = ObjectReference(
                source_object_type=ObjectType.INSTANCE,
                source_uuid=SOURCE_UUID,
                relationship=RelationshipType.DISK,
                relationship_value='0',
                target_object_type=ObjectType.BLOB,
                target_uuid=TARGET_UUID,
                created=1234567890.0,
                last_active=1234567890.0
            )
            ref2 = ObjectReference(
                source_object_type=ObjectType.ARTIFACT,
                source_uuid=SOURCE_UUID,
                relationship=RelationshipType.ARTIFACT_INDEX,
                relationship_value='000000000001',
                target_object_type=ObjectType.BLOB,
                target_uuid=TARGET_UUID,
                created=1234567890.0,
                last_active=1234567890.0
            )
            result = references_to_grouped_dict([ref1, ref2])

            self.assertEqual(len(result), 2)
            self.assertIn('disk', result)
            self.assertIn('artifact_index', result)

    def test_references_to_grouped_dict_namespaced_requester_node_refs(self):
        """Test that a namespaced requester does not see Node references."""
        with mock.patch(
                'shakenfist.schema.object_reference.request_namespace',
                return_value='testns'):
            ref1 = ObjectReference(
                source_object_type=ObjectType.NODE,
                source_uuid='sf-1',
                relationship=RelationshipType.BLOB_LOCATION,
                relationship_value=None,
                target_object_type=ObjectType.BLOB,
                target_uuid=TARGET_UUID,
                created=1234567890.0,
                last_active=1234567890.0
            )
            ref2 = ObjectReference(
                source_object_type=ObjectType.NODE,
                source_uuid=SOURCE_UUID,
                relationship=RelationshipType.INSTANCE_LOCATION,
                relationship_value=None,
                target_object_type=ObjectType.INSTANCE,
                target_uuid=TARGET_UUID,
                created=1234567890.0,
                last_active=1234567890.0
            )
            ref3 = ObjectReference(
                source_object_type=ObjectType.ARTIFACT,
                source_uuid=SOURCE_UUID,
                relationship=RelationshipType.ARTIFACT_INDEX,
                relationship_value='000000000001',
                target_object_type=ObjectType.BLOB,
                target_uuid=TARGET_UUID,
                created=1234567890.0,
                last_active=1234567890.0
            )
            result = references_to_grouped_dict([ref1, ref2, ref3])

            self.assertNotIn('blob_location', result)
            self.assertNotIn('instance_location', result)
            self.assertIn('artifact_index', result)

    def test_references_to_grouped_dict_system_requester_node_refs(self):
        """Test that the system requester sees Node references."""
        with mock.patch(
                'shakenfist.schema.object_reference.request_namespace',
                return_value='system'):
            ref = ObjectReference(
                source_object_type=ObjectType.NODE,
                source_uuid='sf-1',
                relationship=RelationshipType.BLOB_LOCATION,
                relationship_value=None,
                target_object_type=ObjectType.BLOB,
                target_uuid=TARGET_UUID,
                created=1234567890.0,
                last_active=1234567890.0
            )
            result = references_to_grouped_dict([ref])

            self.assertIn('blob_location', result)
            self.assertEqual(len(result['blob_location']), 1)
