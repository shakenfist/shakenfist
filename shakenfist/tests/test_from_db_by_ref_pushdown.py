# Tests for DatabaseBackedObject._from_db_by_ref_pushdown, the shared body
# of the pushed-down from_db_by_ref implementations (issue 4948).
#
# Artifact, Instance and Network each used to carry their own copy of this
# body. The copies differed only in which find_* they called and in how they
# hydrated a row, so this module tests the shared helper directly -- the
# per-type behaviour is still pinned end to end by
# test_artifact_from_db_by_ref.py, test_instance_from_db_by_ref.py and
# test_network_from_db_by_ref.py -- and then walks the tree to assert no
# type has grown a fresh copy.
#
# The namespace collapse is the reason this matters: it is silent when it is
# wrong. A copy which omits it narrows an administrative lookup to the
# objects literally owned by 'system' and answers a plausible wrong object
# rather than raising.

import ast
import pathlib
import uuid
from unittest import mock

from shakenfist import baseobject
from shakenfist import exceptions
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


class _StubObject(baseobject.DatabaseBackedObject):
    """A minimal type to exercise the helper without a real table."""

    object_type = ObjectType.BLOB
    ACTIVE_STATES = {'created', 'creating'}


def _hydrate(row):
    """Stand in for a type's row-to-object conversion."""
    return ('hydrated', row)


class FromDbByRefPushdownTestCase(base.ShakenFistTestCase):
    """Unit tests for DatabaseBackedObject._from_db_by_ref_pushdown."""

    def test_invalid_ref_answers_not_found_without_querying(self):
        """A null or non-string ref never reaches the query (issue 4223)."""
        for ref in [None, '', 42, uuid.uuid4(), {'name': 'foo'}]:
            finder = mock.Mock()
            self.assertIsNone(
                _StubObject._from_db_by_ref_pushdown(
                    ref, 'tenant-a', finder, _hydrate))
            finder.assert_not_called()

    def test_uuid_ref_short_circuits_to_from_db(self):
        """A valid UUID is a from_db lookup, not a name search."""
        object_uuid = str(uuid.uuid4())
        finder = mock.Mock()

        with mock.patch.object(_StubObject, 'from_db') as mock_from_db:
            mock_from_db.return_value = mock.sentinel.object_by_uuid
            result = _StubObject._from_db_by_ref_pushdown(
                object_uuid, 'tenant-a', finder, _hydrate)

        mock_from_db.assert_called_once_with(object_uuid)
        finder.assert_not_called()
        self.assertIs(result, mock.sentinel.object_by_uuid)

    def test_named_namespace_is_forwarded(self):
        """A real namespace scopes the query to that namespace."""
        finder = mock.Mock(return_value=[])

        _StubObject._from_db_by_ref_pushdown(
            'foo', 'tenant-a', finder, _hydrate)

        criteria = finder.call_args[0][0]
        self.assertEqual(criteria.namespace, 'tenant-a')
        self.assertEqual(criteria.name, 'foo')
        self.assertEqual(sorted(criteria.states),
                         sorted(_StubObject.ACTIVE_STATES))

    def test_system_and_none_namespaces_collapse_to_no_filter(self):
        """'system' and None both mean "look across all namespaces"."""
        for namespace in ['system', None]:
            finder = mock.Mock(return_value=[])

            _StubObject._from_db_by_ref_pushdown(
                'foo', namespace, finder, _hydrate)

            criteria = finder.call_args[0][0]
            self.assertIsNone(criteria.namespace)
            self.assertEqual(criteria.name, 'foo')

    def test_no_match_answers_none(self):
        """An unmatched name is an ordinary "not found"."""
        self.assertIsNone(
            _StubObject._from_db_by_ref_pushdown(
                'foo', 'tenant-a', mock.Mock(return_value=[]), _hydrate))

    def test_one_match_is_hydrated_by_the_callback(self):
        """The row goes to the type's hydration callback untouched."""
        row = mock.sentinel.row
        hydrate = mock.Mock(return_value=mock.sentinel.object_by_name)

        result = _StubObject._from_db_by_ref_pushdown(
            'foo', 'tenant-a', mock.Mock(return_value=[row]), hydrate)

        hydrate.assert_called_once_with(row)
        self.assertIs(result, mock.sentinel.object_by_name)

    def test_multiple_matches_raise_naming_the_type(self):
        """An ambiguous name raises rather than picking a winner."""
        finder = mock.Mock(return_value=[mock.sentinel.a, mock.sentinel.b])
        hydrate = mock.Mock()

        with self.assertRaises(exceptions.MultipleObjects) as ctx:
            _StubObject._from_db_by_ref_pushdown(
                'foo', 'tenant-a', finder, hydrate)

        self.assertEqual(
            str(ctx.exception),
            'multiple blobs have the name "foo" in namespace "tenant-a"')
        hydrate.assert_not_called()


class FromDbByRefNoFreshCopiesTestCase(base.ShakenFistTestCase):
    """The namespace-collapse rule must exist in exactly one place.

    Three copies of it drifted apart once already (issue 4948). A fourth
    type which pushes its name lookup down to SQL supplies a find_* and a
    hydration callback to the base-class helper; it does not write the
    criteria itself. If a type genuinely needs a different body, that is a
    decision to make deliberately, and changing this test is how it is
    made.
    """

    # The one legitimate copy: the helper itself, and the Python-side scan
    # it replaces, both live in baseobject.py.
    _EXEMPT = {'baseobject.py'}

    def _source_files(self):
        root = pathlib.Path(baseobject.__file__).parent
        for path in sorted(root.rglob('*.py')):
            relative = path.relative_to(root)
            if relative.parts[0] in ['tests', 'deploy', 'protos']:
                continue
            if path.name in self._EXEMPT:
                continue
            yield path, relative

    def test_every_override_delegates_to_the_helper(self):
        """Each from_db_by_ref override calls _from_db_by_ref_pushdown."""
        overrides = 0

        for path, relative in self._source_files():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                if node.name != 'from_db_by_ref':
                    continue
                overrides += 1

                called = set()
                constructed = set()
                for inner in ast.walk(node):
                    if not isinstance(inner, ast.Call):
                        continue
                    func = inner.func
                    if isinstance(func, ast.Attribute):
                        called.add(func.attr)
                    elif isinstance(func, ast.Name):
                        constructed.add(func.id)

                self.assertIn(
                    '_from_db_by_ref_pushdown', called,
                    f'{relative}:{node.lineno} from_db_by_ref must delegate '
                    'to DatabaseBackedObject._from_db_by_ref_pushdown rather '
                    'than repeat the namespace collapse')
                self.assertNotIn(
                    'ObjectFilterCriteria', constructed,
                    f'{relative}:{node.lineno} from_db_by_ref must not build '
                    'its own ObjectFilterCriteria; the helper does that')

        # Artifact, Instance and Network today. A fourth type is welcome to
        # join them -- that is the point of the helper -- but a walk which
        # finds nothing must not pass vacuously.
        self.assertGreaterEqual(overrides, 3)
