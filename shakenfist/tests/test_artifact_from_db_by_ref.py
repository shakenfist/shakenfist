# Tests for Artifact.from_db_by_ref (phase 2 SQL pushdown filtering).
#
# This module tests:
# - UUID input short-circuits to cls.from_db (find_artifacts NOT called)
# - Non-UUID name with specific namespace passes criteria correctly
# - Non-UUID name with namespace='system' passes criteria.namespace=None
# - Non-UUID name with namespace=None passes criteria.namespace=None
# - Zero matches returns None
# - One match returns an Artifact instance built from the ArtifactData
# - Two matches raises exceptions.MultipleObjects with expected message

import uuid
from unittest import mock

from shakenfist import exceptions
from shakenfist.artifact import Artifact
from shakenfist.schema.artifact_data import ArtifactData
from shakenfist.tests import base


# A valid UUID4 string used for the UUID short-circuit test.
_ARTIFACT_UUID = str(uuid.uuid4())

# A second UUID for two-match scenario.
_ARTIFACT_UUID_2 = str(uuid.uuid4())


def _make_artifact_data(
        art_uuid=None, artifact_type='image',
        source_url='http://example.com/img.qcow2',
        name='foo', namespace='tenant-a', version=9):
    """Construct a real ArtifactData Pydantic instance with minimal fields."""
    return ArtifactData(
        uuid=art_uuid or _ARTIFACT_UUID,
        artifact_type=artifact_type,
        source_url=source_url,
        name=name,
        namespace=namespace,
        version=version,
    )


class ArtifactFromDbByRefTestCase(base.ShakenFistTestCase):
    """Unit tests for Artifact.from_db_by_ref."""

    # ------------------------------------------------------------------
    # Test 1: UUID input short-circuits to cls.from_db
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.artifact.mariadb.find_artifacts')
    @mock.patch.object(Artifact, 'from_db')
    def test_uuid_input_calls_from_db_not_find_artifacts(
            self, mock_from_db, mock_find_artifacts):
        """UUID ref short-circuits to from_db; find_artifacts is never called."""
        sentinel = mock.sentinel.artifact_instance
        mock_from_db.return_value = sentinel

        result = Artifact.from_db_by_ref(_ARTIFACT_UUID)

        mock_from_db.assert_called_once_with(_ARTIFACT_UUID)
        mock_find_artifacts.assert_not_called()
        self.assertIs(result, sentinel)

    # ------------------------------------------------------------------
    # Test 2: Non-UUID name with specific namespace
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.artifact.mariadb.find_artifacts')
    def test_name_with_specific_namespace_passes_correct_criteria(
            self, mock_find_artifacts):
        """Specific namespace is forwarded as criteria.namespace."""
        data = _make_artifact_data(name='foo', namespace='tenant-a')
        mock_find_artifacts.return_value = [data]

        Artifact.from_db_by_ref('foo', namespace='tenant-a')

        mock_find_artifacts.assert_called_once()
        criteria = mock_find_artifacts.call_args[0][0]
        self.assertEqual(sorted(criteria.states),
                         sorted(list(Artifact.ACTIVE_STATES)))
        self.assertEqual(criteria.namespace, 'tenant-a')
        self.assertEqual(criteria.name, 'foo')

    # ------------------------------------------------------------------
    # Test 3: Non-UUID name with namespace='system' → criteria.namespace=None
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.artifact.mariadb.find_artifacts')
    def test_name_with_system_namespace_passes_none_criteria_namespace(
            self, mock_find_artifacts):
        """namespace='system' collapses to criteria.namespace=None."""
        data = _make_artifact_data(name='foo', namespace='system')
        mock_find_artifacts.return_value = [data]

        Artifact.from_db_by_ref('foo', namespace='system')

        criteria = mock_find_artifacts.call_args[0][0]
        self.assertIsNone(criteria.namespace)
        self.assertEqual(criteria.name, 'foo')

    # ------------------------------------------------------------------
    # Test 4: Non-UUID name with namespace=None → criteria.namespace=None
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.artifact.mariadb.find_artifacts')
    def test_name_with_none_namespace_passes_none_criteria_namespace(
            self, mock_find_artifacts):
        """namespace=None collapses to criteria.namespace=None."""
        data = _make_artifact_data(name='foo', namespace='any')
        mock_find_artifacts.return_value = [data]

        Artifact.from_db_by_ref('foo', namespace=None)

        criteria = mock_find_artifacts.call_args[0][0]
        self.assertIsNone(criteria.namespace)
        self.assertEqual(criteria.name, 'foo')

    # ------------------------------------------------------------------
    # Test 5: Zero matches returns None
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.artifact.mariadb.find_artifacts')
    def test_zero_matches_returns_none(self, mock_find_artifacts):
        """find_artifacts returning [] causes from_db_by_ref to return None."""
        mock_find_artifacts.return_value = []

        result = Artifact.from_db_by_ref('no-such-image', namespace='tenant-a')

        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Test 6: Exactly one match returns an Artifact instance
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.artifact.mariadb.find_artifacts')
    def test_one_match_returns_artifact_instance(self, mock_find_artifacts):
        """One ArtifactData returned → Artifact instance with correct name."""
        data = _make_artifact_data(name='foo', namespace='tenant-a')
        mock_find_artifacts.return_value = [data]

        result = Artifact.from_db_by_ref('foo', namespace='tenant-a')

        self.assertIsInstance(result, Artifact)
        self.assertEqual(result.name, 'foo')

    # ------------------------------------------------------------------
    # Test 7: Two matches raises MultipleObjects with expected message
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.artifact.mariadb.find_artifacts')
    def test_two_matches_raises_multiple_objects(self, mock_find_artifacts):
        """Two ArtifactData records → MultipleObjects with name and namespace."""
        data1 = _make_artifact_data(
            art_uuid=_ARTIFACT_UUID, name='foo', namespace='tenant-a')
        data2 = _make_artifact_data(
            art_uuid=_ARTIFACT_UUID_2, name='foo', namespace='tenant-a')
        mock_find_artifacts.return_value = [data1, data2]

        with self.assertRaises(exceptions.MultipleObjects) as ctx:
            Artifact.from_db_by_ref('foo', namespace='tenant-a')

        msg = str(ctx.exception)
        self.assertIn('foo', msg)
        self.assertIn('tenant-a', msg)
