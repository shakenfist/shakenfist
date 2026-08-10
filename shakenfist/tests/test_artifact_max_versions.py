# Copyright 2026 Michael Still and contributors
#
# A negative max_versions is silently destructive rather than merely
# meaningless: delete_old_versions() tests len(indexes) > max, which a
# negative makes always true, and then slices [:-max], so every
# subsequent add_index() drops the oldest surviving version.
#
# The check used to live in ArtifactMaxVersionsEndpoint.post() alone,
# which closed one of three routes writing the attribute -- label
# create and instance snapshot both reach Artifact.new() and then the
# setter with whatever the caller sent. It now lives beside the setter,
# so every writer inherits it whether or not it ever sees a request
# body, and the endpoints translate the refusal into a 400.

from unittest import mock

from shakenfist import exceptions
from shakenfist.artifact import Artifact
from shakenfist.artifact import validated_max_versions
from shakenfist.tests import base


class ValidatedMaxVersionsTestCase(base.ShakenFistTestCase):
    def test_a_whole_number_of_versions_is_accepted(self):
        # Zero is legal and means "the configured default", which is
        # what the getter substitutes.
        for value in (0, 1, 2, '3', 100):
            with self.subTest(value=value):
                self.assertEqual(int(value), validated_max_versions(value))

    def test_a_negative_is_refused(self):
        for value in (-1, -100, '-1'):
            with self.subTest(value=value):
                self.assertRaises(
                    exceptions.InvalidMaxVersions,
                    validated_max_versions, value)

    def test_an_unparsable_value_is_refused(self):
        # int() raises TypeError rather than ValueError for a list or a
        # dict, and a body value reaches here unfiltered, so this path
        # used to serve a 500.
        for value in (['two'], {'two': 2}, 'two', None):
            with self.subTest(value=value):
                self.assertRaises(
                    exceptions.InvalidMaxVersions,
                    validated_max_versions, value)


class MaxVersionsSetterTestCase(base.ShakenFistTestCase):
    """The guard is inherited by every writer, not just the endpoints.

    Asserted against the setter rather than against a route because
    the setter is what all three routes reach, and because a fourth
    caller written later gets the same refusal without anybody having
    to remember to add a check to it.
    """

    @mock.patch('shakenfist.artifact.Artifact.delete_old_versions')
    @mock.patch('shakenfist.artifact.Artifact._update_attributes')
    def _set(self, value, mock_update, mock_delete):
        a = Artifact({
            'uuid': 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',
            'version': Artifact.current_version,
            'artifact_type': Artifact.TYPE_LABEL,
            'source_url': 'sf://label/system/thing',
            'name': 'thing',
            'namespace': 'system'
            })
        a.max_versions = value
        return mock_update, mock_delete

    def test_a_negative_never_reaches_the_database(self):
        self.assertRaises(exceptions.InvalidMaxVersions, self._set, -1)

    def test_a_valid_value_is_written(self):
        # The control: without it the refusal above could be a setter
        # which no longer writes anything at all.
        mock_update, mock_delete = self._set(4)
        mock_update.assert_called_once_with(max_versions=4)
        mock_delete.assert_called_once_with()
