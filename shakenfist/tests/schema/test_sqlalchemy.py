# Tests for schema/sqlalchemy.py Pydantic-to-SQLAlchemy conversion utilities.
#
# This module tests:
# - SQLNativeUUID marker class
# - _has_native_uuid_marker() function
# - pydantic_to_sqlalchemy_table() with native UUID support

from typing import Annotated
from typing import Optional

import annotated_types
import pydantic
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretStr
from pydantic import UUID4
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from shakenfist import exceptions
from shakenfist.schema import sqlalchemy as sqlalchemy_module
from shakenfist.schema.namespace_key_attributes import (
    NamespaceKeyAttributesData)
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLLongText
from shakenfist.schema.sqlalchemy import SQLNativeUUID
from shakenfist.schema.sqlalchemy import SQLUniqueIndex
from shakenfist.schema.sqlalchemy import stored_max_length
from shakenfist.schema.trusted_issuer_attributes import (
    TrustedIssuerAttributesData)
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


class SecretStrColumnTypeTestCase(base.ShakenFistTestCase):
    """SecretStr must produce the same column type as str.

    Wrapping an existing str field in SecretStr is meant to be a change
    of Python type with no effect on the database. If the mapping is ever
    removed, SecretStr falls through to the unknown-type fallback, which
    returns LONGTEXT after only logging a warning -- and because
    _ensure_*_schema() creates tables from the model only when they are
    absent, that would give fresh installs a different column type from
    every upgraded cluster with no schema version change to catch it.
    These tests are what makes that a test failure rather than a silent
    divergence.
    """

    def test_secretstr_maps_to_varchar_255(self):
        col_type = _get_sqlalchemy_type(SecretStr, field_metadata=[])
        self.assertIsInstance(col_type, sa.String)
        self.assertEqual(255, col_type.length)
        self.assertNotIsInstance(col_type, mysql.LONGTEXT)

    def test_secretstr_matches_str_exactly(self):
        secret_type = _get_sqlalchemy_type(SecretStr, field_metadata=[])
        plain_type = _get_sqlalchemy_type(str, field_metadata=[])
        self.assertEqual(
            plain_type.__class__, secret_type.__class__)
        self.assertEqual(plain_type.length, secret_type.length)

    def test_optional_secretstr_maps_to_varchar_255(self):
        col_type = _get_sqlalchemy_type(
            Optional[SecretStr], field_metadata=[])
        self.assertIsInstance(col_type, sa.String)
        self.assertEqual(255, col_type.length)

    def test_secretstr_honours_the_longtext_marker(self):
        col_type = _get_sqlalchemy_type(
            SecretStr, field_metadata=[SQLLongText()])
        self.assertIsInstance(col_type, mysql.LONGTEXT)

    def test_secretstr_field_generates_a_string_column(self):
        # The end to end path, through the table generator rather than
        # the type helper, because that is what mariadb.py calls.
        class SecretModel(BaseModel):
            model_config = ConfigDict(frozen=False)
            uuid: Annotated[UUID4, SQLNativeUUID()]
            plain: Annotated[str, Field(max_length=255)]
            secret: Annotated[SecretStr, Field(max_length=255)]

        metadata = sa.MetaData()
        table = pydantic_to_sqlalchemy_table(
            SecretModel, 'secret_test', metadata,
            primary_key_fields=['uuid'])

        self.assertIsInstance(table.c.secret.type, sa.String)
        self.assertEqual(255, table.c.secret.type.length)
        self.assertEqual(
            table.c.plain.type.__class__, table.c.secret.type.__class__)

    def test_unknown_type_still_falls_back_to_longtext(self):
        # The fallback the SecretStr mapping exists to avoid. Pinned so
        # that the behaviour being guarded against stays visible.
        class NotAKnownType:
            pass

        col_type = _get_sqlalchemy_type(NotAKnownType, field_metadata=[])
        self.assertIsInstance(col_type, mysql.LONGTEXT)

    def test_the_real_namespace_key_table_is_unchanged(self):
        # The tests above use a synthetic model, which proves the
        # mapping but not that the one table which actually has
        # SecretStr fields came out the way it did before they were
        # wrapped. That is the claim which lets
        # NAMESPACE_KEY_ATTRIBUTES_VERSION stay at 1 with no migration,
        # so it is asserted against the real model rather than a stand
        # in.
        metadata = sa.MetaData()
        table = pydantic_to_sqlalchemy_table(
            NamespaceKeyAttributesData, 'namespace_key_attributes_test',
            metadata, primary_key_fields=['uuid'])

        for column in ['key', 'nonce']:
            col_type = table.c[column].type
            self.assertIsInstance(col_type, sa.String, column)
            self.assertEqual(255, col_type.length, column)
            self.assertNotIsInstance(col_type, mysql.LONGTEXT, column)


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
            primary_key_fields=['uuid'], include_id_column=False)

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
            primary_key_fields=['uuid'], include_id_column=False)

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
            primary_key_fields=['uuid'], include_id_column=False)

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
            primary_key_fields=['uuid'], include_id_column=False)

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


class StoredMaxLengthTestCase(base.ShakenFistTestCase):
    """Tests for stored_max_length().

    The helper exists because Field(max_length=...) and the generated
    column's capacity are independent numbers, and validators written
    against the first one accept values the second cannot hold. Every
    assertion here is about the helper answering with the column rather
    than with the declaration.
    """

    def test_a_plain_str_field_gets_a_column_for_its_declaration(self):
        class Model(BaseModel):
            value: Annotated[str, Field(max_length=4096)]

        # Declared 4096 and stored in a VARCHAR(4096), because the
        # column builder reads the declaration. This asserted 255 until
        # it did, which is the defect issue 4901 describes.
        self.assertEqual(4096, stored_max_length(Model, 'value'))

    def test_a_longtext_field_is_bounded_by_its_declaration(self):
        class Model(BaseModel):
            value: Annotated[str, SQLLongText(), Field(max_length=1024)]

        # The marker buys a LONGTEXT, so the declaration is the only
        # limit that applies and it is the true one.
        self.assertEqual(1024, stored_max_length(Model, 'value'))

    def test_a_declaration_under_the_column_wins(self):
        class Model(BaseModel):
            value: Annotated[str, Field(max_length=64)]

        self.assertEqual(64, stored_max_length(Model, 'value'))

    def test_an_unbounded_longtext_field_has_no_limit(self):
        class Model(BaseModel):
            value: Annotated[Optional[str], SQLLongText()] = None

        self.assertIsNone(stored_max_length(Model, 'value'))

    def test_an_undeclared_str_field_is_still_bounded(self):
        class Model(BaseModel):
            value: str

        self.assertEqual(255, stored_max_length(Model, 'value'))

    def test_a_non_string_field_has_no_character_limit(self):
        class Model(BaseModel):
            value: float

        self.assertIsNone(stored_max_length(Model, 'value'))

    def test_the_answer_matches_the_column_the_table_really_gets(self):
        """The property that stops this drifting from the DDL.

        stored_max_length() calls _get_sqlalchemy_type() the same way
        pydantic_to_sqlalchemy_table() does, but "the same way" is the
        sort of claim that stays true until somebody changes one of
        them. So build the table and compare, for every field of a
        model which has both kinds of string column.
        """
        table = pydantic_to_sqlalchemy_table(
            TrustedIssuerAttributesData, 'trusted_issuer_attributes_probe',
            sa.MetaData(), primary_key_fields=['uuid'])

        for name, field in TrustedIssuerAttributesData.model_fields.items():
            declared = next(
                (item.max_length for item in field.metadata
                 if getattr(item, 'max_length', None) is not None), None)
            column_length = table.c[name].type.length if hasattr(
                table.c[name].type, 'length') else None

            expected = [limit for limit in (column_length, declared)
                        if limit is not None]
            self.assertEqual(
                min(expected) if expected else None,
                stored_max_length(TrustedIssuerAttributesData, name),
                f'{name} disagrees with its own column')


class DeclaredColumnWidthTestCase(base.ShakenFistTestCase):
    """The column a str field's declared max_length builds.

    Before issue 4901 every str field got VARCHAR(255) whatever it
    declared, so a field declaring more accepted values its column could
    not hold, and nothing but an author remembering SQLLongText() held
    the two numbers together.
    """

    def _column_type(self, model, field_name='value'):
        table = pydantic_to_sqlalchemy_table(
            model, 'probe_%s' % field_name, sa.MetaData(),
            include_id_column=True)
        return table.c[field_name].type

    def test_a_declaration_over_the_default_widens_the_column(self):
        class Model(BaseModel):
            value: Annotated[str, Field(max_length=1024)]

        self.assertEqual(1024, self._column_type(Model).length)

    def test_a_declaration_under_the_default_keeps_the_default(self):
        """Narrowing is not done, deliberately.

        It would be a migration on most tables in the tree to enforce a
        bound the model enforces first, and MariaDB would then refuse a
        value the model has already refused.
        """
        class Model(BaseModel):
            value: Annotated[str, Field(max_length=16)]

        self.assertEqual(255, self._column_type(Model).length)

    def test_an_undeclared_field_keeps_the_default(self):
        class Model(BaseModel):
            value: str

        self.assertEqual(255, self._column_type(Model).length)

    def test_the_strictest_declaration_is_the_one_built(self):
        class Model(BaseModel):
            value: Annotated[str, Field(max_length=2048),
                             annotated_types.MaxLen(1024)]

        self.assertEqual(1024, self._column_type(Model).length)

    def test_an_optional_field_is_widened_too(self):
        class Model(BaseModel):
            value: Annotated[Optional[str], Field(max_length=1024)] = None

        self.assertEqual(1024, self._column_type(Model).length)

    def test_a_secret_field_is_widened_too(self):
        class Model(BaseModel):
            value: Annotated[SecretStr, Field(max_length=1024)]

        self.assertEqual(1024, self._column_type(Model).length)

    def test_the_longtext_marker_still_wins(self):
        """A marked field is LONGTEXT however wide its declaration.

        The marker is how a field says it wants its value stored away
        from the row, which is not the same question as how long the
        value may be.
        """
        class Model(BaseModel):
            value: Annotated[str, SQLLongText(), Field(max_length=1024)]

        self.assertIsInstance(self._column_type(Model), mysql.LONGTEXT)

    def test_a_declaration_wider_than_a_varchar_is_refused(self):
        """Rather than silently emitting LONGTEXT.

        A LONGTEXT column cannot be indexed without a prefix length, so
        the choice stays with whoever writes the model.
        """
        class Model(BaseModel):
            value: Annotated[str, Field(
                max_length=sqlalchemy_module.MAX_VARCHAR_LENGTH + 1)]

        self.assertRaises(
            exceptions.InvalidSchemaDeclaration, self._column_type, Model)

    def test_the_widest_varchar_is_allowed(self):
        class Model(BaseModel):
            value: Annotated[str, Field(
                max_length=sqlalchemy_module.MAX_VARCHAR_LENGTH)]

        # Alone in its row: with the BIGINT id column beside it the row
        # is 65,542 bytes, which MariaDB refuses and so does the row
        # check below.
        table = pydantic_to_sqlalchemy_table(
            Model, 'probe_widest', sa.MetaData(), include_id_column=False)
        self.assertEqual(
            sqlalchemy_module.MAX_VARCHAR_LENGTH, table.c['value'].type.length)


class TableLimitsTestCase(base.ShakenFistTestCase):
    """The limits a table has that none of its fields has alone.

    MAX_VARCHAR_LENGTH bounds one field. MariaDB also bounds the sum of
    a row's columns, and InnoDB the sum of an index's key parts, and
    once a declaration can widen a column both are reachable from a
    model. CREATE TABLE refusing them would only ever happen on a fresh
    install, so the builder refuses them first.
    """

    def _build(self, model, **kwargs):
        return pydantic_to_sqlalchemy_table(
            model, 'probe_limits', sa.MetaData(), **kwargs)

    def test_an_indexed_field_wider_than_an_index_key_is_refused(self):
        class Model(BaseModel):
            value: Annotated[str, SQLIndex(), Field(max_length=1024)]

        with self.assertRaises(exceptions.InvalidSchemaDeclaration) as ctx:
            self._build(Model)
        self.assertIn('idx_probe_limits_value', str(ctx.exception))
        self.assertIn('768', str(ctx.exception))

    def test_a_unique_index_is_held_to_the_same_limit(self):
        class Model(BaseModel):
            value: Annotated[str, SQLUniqueIndex(), Field(max_length=769)]

        self.assertRaises(
            exceptions.InvalidSchemaDeclaration, self._build, Model)

    def test_the_widest_indexable_field_is_allowed(self):
        class Model(BaseModel):
            value: Annotated[str, SQLIndex(), Field(max_length=768)]

        table = self._build(Model)
        self.assertEqual(768, table.c['value'].type.length)

    def test_a_compound_index_is_limited_by_the_sum_of_its_columns(self):
        """Each column fits a key alone; together they do not."""
        class Model(BaseModel):
            model_config = ConfigDict(
                json_schema_extra={'sql_indexes': [('first', 'second')]})
            first: Annotated[str, Field(max_length=512)]
            second: Annotated[str, Field(max_length=512)]

        with self.assertRaises(exceptions.InvalidSchemaDeclaration) as ctx:
            self._build(Model)
        self.assertIn('idx_probe_limits_first_second', str(ctx.exception))

    def test_an_index_passed_by_the_caller_is_checked(self):
        class Model(BaseModel):
            value: Annotated[str, Field(max_length=1024)]

        self.assertRaises(
            exceptions.InvalidSchemaDeclaration, self._build, Model,
            indexes=[('value',)])

    def test_a_primary_key_is_an_index_too(self):
        class Model(BaseModel):
            value: Annotated[str, Field(max_length=1024)]

        with self.assertRaises(exceptions.InvalidSchemaDeclaration) as ctx:
            self._build(Model, primary_key_fields=['value'])
        self.assertIn('PRIMARY', str(ctx.exception))

    def test_an_indexed_longtext_field_is_refused(self):
        class Model(BaseModel):
            value: Annotated[str, SQLIndex(), SQLLongText()]

        self.assertRaises(
            exceptions.InvalidSchemaDeclaration, self._build, Model)

    def test_columns_which_each_fit_but_together_overflow_are_refused(self):
        class Model(BaseModel):
            first: Annotated[str, Field(max_length=10000)]
            second: Annotated[str, Field(max_length=10000)]

        with self.assertRaises(exceptions.InvalidSchemaDeclaration) as ctx:
            self._build(Model)
        self.assertIn('row limit', str(ctx.exception))

    def test_a_longtext_column_costs_the_row_only_its_pointer(self):
        """Which is why marking a field SQLLongText() is the way out."""
        class Model(BaseModel):
            first: Annotated[str, Field(max_length=10000)]
            second: Annotated[str, SQLLongText(), Field(max_length=10000)]

        self._build(Model)

    def test_the_row_limit_counts_the_null_bitmap(self):
        """At the boundary, where the bitmap's bytes decide it.

        VARCHAR(16365) is 65,460 bytes plus a two byte length prefix,
        and nine booleans are costed at eight bytes each: 65,534. Nine
        nullable booleans need a two byte null bitmap, and 65,536 is
        one byte over.
        """
        flags = ['flag%d' % i for i in range(9)]
        fits = pydantic.create_model(
            'Fits', first=(Annotated[str, Field(max_length=16365)], ...),
            **{flag: (bool, ...) for flag in flags})
        one_over = pydantic.create_model(
            'OneOver', first=(Annotated[str, Field(max_length=16365)], ...),
            **{flag: (Optional[bool], None) for flag in flags})

        self._build(fits, include_id_column=False)
        self.assertRaises(
            exceptions.InvalidSchemaDeclaration, self._build, one_over,
            include_id_column=False)

    def test_a_column_type_with_no_cost_is_refused_not_guessed(self):
        """A guess would be a check that silently stopped checking.

        DATETIME is the shape that reaches this: the builder does not
        make one today, so a hand-registered table moved onto it would
        land here, and the message says what to add.
        """
        with self.assertRaises(exceptions.InvalidSchemaDeclaration) as ctx:
            sqlalchemy_module._check_table_limits(
                'probe', [sa.Column('when', sa.DateTime())], [])
        self.assertIn('_column_bytes()', str(ctx.exception))
