# Tests for schema/sqlalchemy.py Pydantic-to-SQLAlchemy conversion utilities.
#
# This module tests:
# - SQLNativeUUID marker class
# - _has_native_uuid_marker() function
# - pydantic_to_sqlalchemy_table() with native UUID support

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4
import sqlalchemy as sa

from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID
from shakenfist.schema.sqlalchemy import SQLUniqueIndex
from shakenfist.schema.sqlalchemy import _get_sqlalchemy_type
from shakenfist.schema.sqlalchemy import _has_native_uuid_marker
from shakenfist.tests import base


class SQLNativeUUIDMarkerTestCase(base.ShakenFistTestCase):
    """Tests for the SQLNativeUUID marker class."""

    def test_marker_in_metadata(self):
        """Test that SQLNativeUUID is detected in field metadata."""
        metadata = [SQLNativeUUID()]
        self.assertTrue(_has_native_uuid_marker(metadata))

    def test_marker_not_in_metadata(self):
        """Test that absence of SQLNativeUUID is detected."""
        metadata = [SQLIndex()]
        self.assertFalse(_has_native_uuid_marker(metadata))

    def test_marker_with_other_markers(self):
        """Test SQLNativeUUID detection with multiple markers."""
        metadata = [SQLIndex(), SQLNativeUUID(), SQLUniqueIndex()]
        self.assertTrue(_has_native_uuid_marker(metadata))

    def test_empty_metadata(self):
        """Test empty metadata list."""
        self.assertFalse(_has_native_uuid_marker([]))


class GetSQLAlchemyTypeTestCase(base.ShakenFistTestCase):
    """Tests for _get_sqlalchemy_type() function."""

    def test_uuid_without_native_marker(self):
        """Test that UUID without SQLNativeUUID uses String(36)."""
        col_type = _get_sqlalchemy_type(UUID4, field_metadata=[])
        self.assertIsInstance(col_type, sa.String)
        self.assertEqual(col_type.length, 36)

    def test_uuid_with_native_marker(self):
        """Test that UUID with SQLNativeUUID uses sa.Uuid."""
        col_type = _get_sqlalchemy_type(UUID4, field_metadata=[SQLNativeUUID()])
        self.assertIsInstance(col_type, sa.Uuid)

    def test_uuid_with_index_and_native(self):
        """Test UUID with both SQLIndex and SQLNativeUUID markers."""
        col_type = _get_sqlalchemy_type(
            UUID4, field_metadata=[SQLIndex(), SQLNativeUUID()])
        self.assertIsInstance(col_type, sa.Uuid)


class PydanticToSQLAlchemyTableTestCase(base.ShakenFistTestCase):
    """Tests for pydantic_to_sqlalchemy_table() function."""

    def test_native_uuid_column(self):
        """Test that SQLNativeUUID marker creates native UUID column."""
        class TestModel(BaseModel):
            model_config = ConfigDict(frozen=True)
            uuid: Annotated[UUID4, SQLNativeUUID()]
            name: str

        metadata = sa.MetaData()
        table = pydantic_to_sqlalchemy_table(
            TestModel, 'test_table', metadata,
            primary_key_field='uuid', include_id_column=False)

        # Check that uuid column uses native UUID type
        uuid_col = table.c.uuid
        self.assertIsInstance(uuid_col.type, sa.Uuid)

    def test_string_uuid_column(self):
        """Test that UUID without marker uses String(36)."""
        class TestModel(BaseModel):
            model_config = ConfigDict(frozen=True)
            uuid: UUID4
            name: str

        metadata = sa.MetaData()
        table = pydantic_to_sqlalchemy_table(
            TestModel, 'test_table', metadata,
            primary_key_field='uuid', include_id_column=False)

        # Check that uuid column uses String(36)
        uuid_col = table.c.uuid
        self.assertIsInstance(uuid_col.type, sa.String)
        self.assertEqual(uuid_col.type.length, 36)

    def test_native_uuid_with_index(self):
        """Test native UUID column with index."""
        class TestModel(BaseModel):
            model_config = ConfigDict(frozen=True)
            uuid: Annotated[UUID4, SQLNativeUUID()]
            other_uuid: Annotated[UUID4, SQLNativeUUID(), SQLIndex()]

        metadata = sa.MetaData()
        table = pydantic_to_sqlalchemy_table(
            TestModel, 'test_table', metadata,
            primary_key_field='uuid', include_id_column=False)

        # Both should be native UUID type
        self.assertIsInstance(table.c.uuid.type, sa.Uuid)
        self.assertIsInstance(table.c.other_uuid.type, sa.Uuid)

        # other_uuid should have an index
        index_names = [idx.name for idx in table.indexes]
        self.assertIn('idx_test_table_other_uuid', index_names)


class UploadDataTableGenerationTestCase(base.ShakenFistTestCase):
    """Tests for generating the uploads table from UploadData model."""

    def test_uploads_table_schema(self):
        """Test that UploadData generates correct table schema."""
        from shakenfist.schema.upload import UploadData

        metadata = sa.MetaData()
        table = pydantic_to_sqlalchemy_table(
            UploadData, 'uploads', metadata,
            primary_key_field='uuid', include_id_column=False)

        # Check columns exist
        self.assertIn('uuid', table.c)
        self.assertIn('node', table.c)
        self.assertIn('created_at', table.c)
        self.assertIn('version', table.c)

        # uuid should be native UUID (has SQLNativeUUID marker)
        self.assertIsInstance(table.c.uuid.type, sa.Uuid)

        # uuid should be primary key
        self.assertTrue(table.c.uuid.primary_key)

        # Check indexes exist
        index_names = [idx.name for idx in table.indexes]
        self.assertIn('idx_uploads_node', index_names)
        self.assertIn('idx_uploads_created_at', index_names)
