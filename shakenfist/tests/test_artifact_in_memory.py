# Copyright 2026 Michael Still and contributors
#
# In-memory only artifacts (blob-reference images) must never touch the
# database. Before issue 3532 every such artifact wrote an
# object_states row (via the base class primary state path) and an
# artifact_attributes row (via the max_versions setter); both rows were
# orphaned forever because hard_delete() early-returns for in-memory
# objects and state-driven iterators skip objects with no static row.
# sfcbr had accumulated 22,033 leaked state rows and 11,523 leaked
# attribute rows in 16 days of CI.

from unittest import mock

from shakenfist.artifact import Artifact
from shakenfist.artifact import BLOB_URL
from shakenfist.tests import base


class InMemoryArtifactTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.mariadb.update_artifact_attributes')
    @mock.patch('shakenfist.mariadb.create_artifact_attributes')
    @mock.patch('shakenfist.mariadb.get_artifact_attributes')
    @mock.patch('shakenfist.mariadb.set_state')
    @mock.patch('shakenfist.mariadb.get_state')
    def test_in_memory_artifact_writes_no_rows(
            self, mock_get_state, mock_set_state, mock_get_attrs,
            mock_create_attrs, mock_update_attrs):
        a = Artifact.new(
            Artifact.TYPE_IMAGE, BLOB_URL + 'abc123', namespace='system')

        self.assertTrue(a.in_memory_only)
        self.assertEqual(Artifact.STATE_INITIAL, a.state.value)
        self.assertNotEqual(0, a.max_versions)

        mock_get_state.assert_not_called()
        mock_set_state.assert_not_called()
        mock_get_attrs.assert_not_called()
        mock_create_attrs.assert_not_called()
        mock_update_attrs.assert_not_called()
