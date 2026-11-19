# Copyright 2026 Michael Still and contributors
#
# Pins the target declaration of every cluster operation schema. See
# "Cluster Operation Tracking" in
# docs/developer_guide/database_internals.md.
#
# enqueue_cluster_operation() derives the cluster_operation_targets
# rows it writes from the model's target_fields declaration. The
# declaration is read with a silent default, so a schema which does
# not declare it -- or declares it wrongly -- enqueues an operation
# with no target rows at all and nothing reports it. Every by-target
# reader then answers that nothing is happening to the object:
# has_pending_cluster_operation() returns False, Network.is_okay()
# lets the maintainer run its recreate path against an object an
# operation is modifying, the per-object /clusteroperations listing is
# empty, and the CI await helpers return immediately.
#
# Raising at enqueue time instead would turn a reporting gap into a
# runtime failure on a path which creates work, and would fire in
# production rather than in review. So the obligation is pinned here,
# the same way test_observational_tasks.py pins the sibling
# observational_tasks declaration.

import ast
import glob
import importlib
import os
import pkgutil
import tempfile
from typing import ClassVar

from pydantic import BaseModel

import shakenfist
from shakenfist.operations.clusteroperationmapping import (
    OPERATION_NAMES_TO_CLASSES)
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema import operations
from shakenfist.tests import base


GUIDE = ('see "Cluster Operation Tracking" in '
         'docs/developer_guide/database_internals.md')


def _operation_models():
    """Return {module name: model class} for every schema operation."""
    models = {}
    for info in pkgutil.iter_modules(operations.__path__):
        module = importlib.import_module(
            f'{operations.__name__}.{info.name}')
        model = getattr(module, 'model', None)
        if model is None:
            # util and baseclusteroperation define no operation model.
            continue
        models[info.name] = model
    return models


def _declared_target_fields(model):
    """The resolved value of the declaration, not merely its presence.

    Checked as a value rather than with hasattr() because of the
    ClassVar trap: a declaration written without the ClassVar
    annotation becomes a pydantic model field, which is removed from
    the class namespace, so the attribute is absent and
    enqueue_cluster_operation's getattr() default reads as empty.
    Either shape must fail, and only the resolved value sees both.
    """
    return getattr(model, 'target_fields', {})


def _enqueue_calls_missing_model_class(schema_operations_root):
    """Every enqueue_cluster_operation() call must name its model.

    model_class is the other half of the same silent default: it too
    defaults to None, and an enqueue which does not pass it records no
    target rows however well the schema declares target_fields.
    Returns a list of (filename, line number) pairs.

    Parsed with ``ast`` rather than matched as text, so a model_class
    named only in a comment or a docstring beside the call does not
    satisfy it.
    """
    missing = []
    pattern = os.path.join(schema_operations_root, '*.py')
    for path in sorted(glob.glob(pattern)):
        tree = ast.parse(open(path).read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, 'id', None) or getattr(func, 'attr', None)
            if name != 'enqueue_cluster_operation':
                continue

            named = False
            explicit_none = False
            for keyword in node.keywords:
                if keyword.arg == 'model_class':
                    named = True
                    explicit_none = (
                        isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is None)
                elif keyword.arg is None:
                    # **kwargs forwarding could carry it, so treat that
                    # as passed rather than reporting a call we cannot
                    # read.
                    named = True

            if not named or explicit_none:
                missing.append((os.path.basename(path), node.lineno))
    return missing


class TargetFieldsPinTestCase(base.ShakenFistTestCase):
    """Every operation schema declares the objects it targets."""

    def test_operation_models_are_found(self):
        # Guard against an import-path mistake making the tests below
        # pass vacuously over an empty set of models.
        # Derived rather than counted: every operation type the
        # dispatcher knows must have a schema model the scan found.
        models = _operation_models()
        self.assertEqual(
            set(OPERATION_NAMES_TO_CLASSES), set(models), sorted(models))

    def test_every_model_declares_target_fields(self):
        for name, model in _operation_models().items():
            with self.subTest(model=name):
                self.assertTrue(
                    _declared_target_fields(model),
                    f'{name} declares no target_fields, so enqueueing '
                    f'one records no cluster_operation_targets rows and '
                    f'every by-target reader answers that nothing is '
                    f'happening to the object; {GUIDE}')

    def test_declaration_is_not_a_field(self):
        for name, model in _operation_models().items():
            with self.subTest(model=name):
                # A declaration missing its ClassVar annotation would
                # be a pydantic field, invisible to
                # enqueue_cluster_operation's getattr().
                self.assertNotIn(
                    'target_fields', model.model_fields,
                    f'{name} declares target_fields without ClassVar, so '
                    f'pydantic made it a field and no target rows are '
                    f'written; {GUIDE}')

    def test_declared_fields_exist_on_the_model(self):
        for name, model in _operation_models().items():
            for field_name in sorted(_declared_target_fields(model)):
                with self.subTest(model=name, field=field_name):
                    # enqueue_cluster_operation reads the value with
                    # metadata.get(field_name) and skips a None, so a
                    # misspelled field name records nothing rather
                    # than failing.
                    self.assertIn(
                        field_name, model.model_fields,
                        f'{name}.target_fields names {field_name}, which '
                        f'is not a field of the model, so no target row '
                        f'is ever written for it; {GUIDE}')

    def test_declared_object_types_are_targetable(self):
        for name, model in _operation_models().items():
            for field_name, object_type in sorted(
                    _declared_target_fields(model).items()):
                with self.subTest(model=name, field=field_name):
                    self.assertIsInstance(
                        object_type, ObjectType,
                        f'{name}.target_fields maps {field_name} to '
                        f'{object_type!r}, which is not an ObjectType '
                        f'member; {GUIDE}')
                    # Nodes are execution context, not targets. A
                    # node_uuid says where an operation runs, and
                    # Node does not inherit
                    # DatabaseBackedObjectWithOperations, so a NODE
                    # target row has no reader to answer. net_op
                    # carries a node_uuid for the coalescing key for
                    # exactly that reason and deliberately leaves it
                    # out of this declaration.
                    self.assertNotEqual(
                        ObjectType.NODE, object_type,
                        f'{name}.target_fields maps {field_name} to NODE; '
                        f'a node identifies where an operation runs, not '
                        f'what it targets; {GUIDE}')

    def test_every_enqueue_passes_its_model_class(self):
        root = os.path.dirname(os.path.abspath(shakenfist.__file__))
        missing = _enqueue_calls_missing_model_class(
            os.path.join(root, 'schema', 'operations'))

        self.assertEqual(
            [], missing,
            f'enqueue_cluster_operation() call(s) with no model_class: '
            f'{missing}. Without it the target declaration is never '
            f'read and no cluster_operation_targets rows are written; '
            f'{GUIDE}')


class TargetFieldsResolutionTestCase(base.ShakenFistTestCase):
    """Mutation coverage for the declaration reader itself.

    The pins above run over the real schemas, which are clean today,
    so these prove the reader actually reports both ways a declaration
    goes missing -- and in particular that reading the resolved value
    rather than hasattr() is what catches the second one.
    """

    def test_a_model_with_no_declaration_resolves_empty(self):
        class _NoDeclaration(BaseModel):
            uuid: str

        self.assertEqual({}, _declared_target_fields(_NoDeclaration))

    def test_a_declaration_without_classvar_resolves_empty(self):
        class _MissingClassVar(BaseModel):
            target_fields: dict = {'network_uuid': ObjectType.NETWORK}

        # pydantic made it a model field and removed it from the class
        # namespace, so the attribute the enqueue reads is absent and
        # it writes no target rows. hasattr() on the *instance* would
        # find it and report the declaration present.
        self.assertIn('target_fields', _MissingClassVar.model_fields)
        self.assertEqual({}, _declared_target_fields(_MissingClassVar))

    def test_a_declared_model_resolves_to_its_declaration(self):
        class _Declared(BaseModel):
            target_fields: ClassVar[dict[str, ObjectType]] = {
                'network_uuid': ObjectType.NETWORK,
            }
            network_uuid: str

        self.assertEqual(
            {'network_uuid': ObjectType.NETWORK},
            _declared_target_fields(_Declared))


class TargetFieldsSweepTestCase(base.ShakenFistTestCase):
    """Mutation coverage for the model_class sweep itself.

    Proves the sweep reports a real omission, rather than only ever
    returning an empty list because the real tree is clean today.
    """

    def _sweep(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'fake_op.py'), 'w') as f:
                f.write(body)
            return _enqueue_calls_missing_model_class(tmp)

    def test_a_call_without_model_class_is_reported(self):
        self.assertEqual(
            [('fake_op.py', 1)],
            self._sweep('enqueue_cluster_operation(object_type, md)\n'))

    def test_an_explicit_none_is_reported(self):
        self.assertEqual(
            [('fake_op.py', 1)],
            self._sweep(
                'enqueue_cluster_operation(ot, md, model_class=None)\n'))

    def test_a_mention_is_not_a_call_argument(self):
        # A model_class named in a comment or a string beside the call
        # must not satisfy the sweep; a text search would accept both.
        for body in (
                '# model_class=model is still to be added\n'
                'enqueue_cluster_operation(ot, md)\n',
                'DOC = "pass model_class=model"\n'
                'enqueue_cluster_operation(ot, md)\n'):
            self.assertEqual(
                1, len(self._sweep(body)),
                f'a mention rather than an argument was accepted: '
                f'{body!r}')

    def test_a_call_with_model_class_is_accepted(self):
        self.assertEqual(
            [],
            self._sweep(
                'enqueue_cluster_operation(ot, md, model_class=model)\n'))

    def test_a_qualified_call_is_examined(self):
        # The helper is imported by name today, but a util.enqueue_...
        # call must be read too.
        self.assertEqual(
            [('fake_op.py', 1)],
            self._sweep('util.enqueue_cluster_operation(ot, md)\n'))
