# Copyright 2026 Michael Still and contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Every schema model's declared string limits, against its columns.

Field(max_length=...) says what a model accepts and the generated
column says what MariaDB stores. Issue 4901 is what happens when they
are allowed to be different numbers: four fields declared more than
their column held, so a value the model accepted could not be written,
and the only thing holding the two together was whoever remembered to
add SQLLongText() as well.

_get_sqlalchemy_type() now builds the column from the declaration, so
this is a derived test rather than a list to maintain: it walks every
Pydantic model under shakenfist/schema and asserts the property, which
is the form that catches the next model as well as today's.
"""

import importlib
import pkgutil

from enum import Enum
from typing import Annotated

import annotated_types
from pydantic import BaseModel
from pydantic import Field

import sqlalchemy as sa

from shakenfist import mariadb
import shakenfist.schema
from shakenfist.schema.blob_hash import BlobHash
from shakenfist.schema.sqlalchemy import _declared_max_length
from shakenfist.schema.sqlalchemy import _get_sqlalchemy_type
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
from shakenfist.tests import base


def _schema_models():
    """Every Pydantic model defined under shakenfist/schema.

    Keyed by qualified name so a model re-exported by a second module
    is inspected once, and found by walking the package rather than
    from a list, because a list is the thing that goes stale.
    """
    models = {}
    for module_info in pkgutil.walk_packages(
            shakenfist.schema.__path__, 'shakenfist.schema.'):
        module = importlib.import_module(module_info.name)
        for name in dir(module):
            candidate = getattr(module, name)
            if not isinstance(candidate, type):
                continue
            if not issubclass(candidate, BaseModel) or candidate is BaseModel:
                continue
            if not candidate.__module__.startswith('shakenfist.schema'):
                continue
            models['%s.%s' % (candidate.__module__, candidate.__name__)] = \
                candidate
    return models


def _over_declared(models):
    """(model, field, declared, column) for every field over its column.

    A column with no length -- LONGTEXT, a number, a boolean -- bounds
    nothing, so a declaration against it cannot be too large.
    """
    findings = []
    inspected = 0
    for full_name, model in sorted(models.items()):
        for field_name, field in model.model_fields.items():
            declared = _declared_max_length(field.metadata)
            if declared is None:
                continue
            inspected += 1
            column_length = getattr(
                _get_sqlalchemy_type(field.annotation, field.metadata),
                'length', None)
            if column_length is not None and declared > column_length:
                findings.append(
                    (full_name, field_name, declared, column_length))
    return findings, inspected


class DeclaredLimitsTestCase(base.ShakenFistTestCase):

    def test_no_model_declares_more_than_its_column_holds(self):
        models = _schema_models()

        # A floor rather than an equality, so adding a model does not
        # fail this, but deleting the discovery or breaking the import
        # walk cannot leave the assertion below vacuously true.
        self.assertGreater(
            len(models), 40,
            'found %d schema models, so the package walk is broken'
            % len(models))

        findings, inspected = _over_declared(models)
        self.assertGreater(
            inspected, 30,
            'inspected only %d declared limits, so this test is not '
            'looking at the schema it claims to' % inspected)

        self.assertEqual(
            [], findings,
            'these fields accept values their column cannot store; widen '
            'the declaration into the column by raising max_length, mark '
            'the field SQLLongText(), or lower the declaration to what '
            'the column holds: %s'
            % ', '.join('%s.%s declares %d into VARCHAR(%d)' % finding
                        for finding in findings))

    def test_the_sweep_finds_a_field_it_should_refuse(self):
        """The positive control, and a real residual.

        Without a control the assertion above passes equally well when
        _over_declared() has stopped looking, which is how a test like
        this dies.

        The model here is not contrived: _get_sqlalchemy_type() reads a
        declared max_length for str and SecretStr only, so an enum field
        -- VARCHAR(64), whatever it declares -- is a shape the widening
        does not cover. Nothing in the tree declares a limit on one
        today, and this sweep is what would notice if something did.
        """
        class Colour(str, Enum):
            red = 'red'

        class Model(BaseModel):
            value: Annotated[Colour, annotated_types.MaxLen(128)]

        findings, inspected = _over_declared({'probe.Model': Model})

        self.assertEqual(1, inspected)
        self.assertEqual([('probe.Model', 'value', 128, 64)], findings)

    def test_blob_hashes_still_generates_the_column_it_was_created_with(self):
        """The other half of the fix, and the half a sweep cannot see.

        BlobHash.hash_value declared 256 against a VARCHAR(255), and
        once the column builder reads the declaration, leaving it at 256
        silently makes the generated DDL VARCHAR(256) -- a schema change
        to a table with a row per blob per node per algorithm, where
        widening past 255 changes the stored length prefix and rebuilds
        the whole table. It was lowered to 255 instead, because 256 was
        a generous bound on a hex digest rather than a requirement and
        the longest digest stored is sha512's 128 characters.

        So this asserts the DDL did not move and the table's schema
        version did not need to. Raising the declaration again is
        allowed, but it is a migration, and this test is where that is
        said.
        """
        table = pydantic_to_sqlalchemy_table(
            BlobHash, 'blob_hashes_probe', sa.MetaData(),
            primary_key_fields=['blob_uuid', 'node', 'algorithm'],
            include_id_column=False)

        self.assertEqual(255, table.c['hash_value'].type.length)
        self.assertEqual(2, mariadb.EXPECTED_SCHEMA_VERSIONS['blob_hashes'])

    def test_the_strictest_declaration_is_the_one_compared(self):
        """Two limits on one field, which pydantic resolves to the lower.

        Comparing the other one would report a field as over-declared
        when the model refuses the long value anyway.
        """
        class Model(BaseModel):
            value: Annotated[str, Field(max_length=4096),
                             annotated_types.MaxLen(128)]

        findings, inspected = _over_declared({'probe.Model': Model})
        self.assertEqual(1, inspected)
        self.assertEqual([], findings)
