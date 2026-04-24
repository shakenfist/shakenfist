# Tests for Instance.from_db_by_ref (phase 3 SQL pushdown filtering).
#
# This module tests:
# - UUID input short-circuits to cls.from_db (find_instances NOT called)
# - Non-UUID name with specific namespace passes criteria correctly
# - Non-UUID name with namespace='system' passes criteria.namespace=None
# - Non-UUID name with namespace=None passes criteria.namespace=None
# - Zero matches returns None
# - One match returns an Instance instance built via _static_values_to_dict
# - Two matches raises exceptions.MultipleObjects with expected message
#
# Constructor side-effects: Instance.__init__ validates that disk_spec is
# non-empty (raises InstanceBadDiskSpecification otherwise). All InstanceData
# fixtures include a minimal disk_spec to satisfy this. No other DB calls are
# made by the constructor when version == Instance.current_version.

import uuid
from unittest import mock

from shakenfist import exceptions
from shakenfist.instance import Instance
from shakenfist.schema.instance_data import InstanceData
from shakenfist.tests import base


# A valid UUID4 string used for the UUID short-circuit test.
_INSTANCE_UUID = str(uuid.uuid4())

# A second UUID for two-match scenario.
_INSTANCE_UUID_2 = str(uuid.uuid4())

# Minimal disk spec required to pass Instance.__init__ validation.
_DISK_SPEC = [{'size': 8, 'base': 'ubuntu:22.04', 'type': 'disk', 'bus': 'virtio'}]


def _make_instance_data(
        inst_uuid=None, name='foo', namespace='tenant-a',
        cpus=1, memory=1024, disk_spec=None):
    """Construct a real InstanceData Pydantic instance with minimal fields."""
    return InstanceData(
        uuid=inst_uuid or _INSTANCE_UUID,
        cpus=cpus,
        disk_spec=disk_spec if disk_spec is not None else _DISK_SPEC,
        memory=memory,
        name=name,
        namespace=namespace,
        version=Instance.current_version,
    )


class InstanceFromDbByRefTestCase(base.ShakenFistTestCase):
    """Unit tests for Instance.from_db_by_ref."""

    # ------------------------------------------------------------------
    # Test 1: UUID input short-circuits to cls.from_db
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.instance.mariadb.find_instances')
    @mock.patch.object(Instance, 'from_db')
    def test_uuid_input_calls_from_db_not_find_instances(
            self, mock_from_db, mock_find_instances):
        """UUID ref short-circuits to from_db; find_instances is never called."""
        sentinel = mock.sentinel.instance_obj
        mock_from_db.return_value = sentinel

        result = Instance.from_db_by_ref(_INSTANCE_UUID)

        mock_from_db.assert_called_once_with(_INSTANCE_UUID)
        mock_find_instances.assert_not_called()
        self.assertIs(result, sentinel)

    # ------------------------------------------------------------------
    # Test 2: Non-UUID name with specific namespace
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.instance.mariadb.find_instances')
    def test_name_with_specific_namespace_passes_correct_criteria(
            self, mock_find_instances):
        """Specific namespace is forwarded as criteria.namespace."""
        data = _make_instance_data(name='foo', namespace='tenant-a')
        mock_find_instances.return_value = [data]

        Instance.from_db_by_ref('foo', namespace='tenant-a')

        mock_find_instances.assert_called_once()
        criteria = mock_find_instances.call_args[0][0]
        self.assertEqual(sorted(criteria.states),
                         sorted(list(Instance.ACTIVE_STATES)))
        self.assertEqual(criteria.namespace, 'tenant-a')
        self.assertEqual(criteria.name, 'foo')

    # ------------------------------------------------------------------
    # Test 3: Non-UUID name with namespace='system' → criteria.namespace=None
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.instance.mariadb.find_instances')
    def test_name_with_system_namespace_passes_none_criteria_namespace(
            self, mock_find_instances):
        """namespace='system' collapses to criteria.namespace=None."""
        data = _make_instance_data(name='foo', namespace='system')
        mock_find_instances.return_value = [data]

        Instance.from_db_by_ref('foo', namespace='system')

        criteria = mock_find_instances.call_args[0][0]
        self.assertIsNone(criteria.namespace)
        self.assertEqual(criteria.name, 'foo')

    # ------------------------------------------------------------------
    # Test 4: Non-UUID name with namespace=None → criteria.namespace=None
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.instance.mariadb.find_instances')
    def test_name_with_none_namespace_passes_none_criteria_namespace(
            self, mock_find_instances):
        """namespace=None collapses to criteria.namespace=None."""
        data = _make_instance_data(name='foo', namespace='any')
        mock_find_instances.return_value = [data]

        Instance.from_db_by_ref('foo', namespace=None)

        criteria = mock_find_instances.call_args[0][0]
        self.assertIsNone(criteria.namespace)
        self.assertEqual(criteria.name, 'foo')

    # ------------------------------------------------------------------
    # Test 5: Zero matches returns None
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.instance.mariadb.find_instances')
    def test_zero_matches_returns_none(self, mock_find_instances):
        """find_instances returning [] causes from_db_by_ref to return None."""
        mock_find_instances.return_value = []

        result = Instance.from_db_by_ref('no-such-vm', namespace='tenant-a')

        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Test 6: Exactly one match returns an Instance instance
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.instance.mariadb.find_instances')
    def test_one_match_returns_instance_object(self, mock_find_instances):
        """One InstanceData returned → Instance instance with correct name."""
        data = _make_instance_data(name='foo', namespace='tenant-a')
        mock_find_instances.return_value = [data]

        result = Instance.from_db_by_ref('foo', namespace='tenant-a')

        self.assertIsInstance(result, Instance)
        self.assertEqual(result.name, 'foo')

    # ------------------------------------------------------------------
    # Test 7: Two matches raises MultipleObjects with expected message
    # ------------------------------------------------------------------

    @mock.patch('shakenfist.instance.mariadb.find_instances')
    def test_two_matches_raises_multiple_objects(self, mock_find_instances):
        """Two InstanceData records → MultipleObjects with name and namespace."""
        data1 = _make_instance_data(
            inst_uuid=_INSTANCE_UUID, name='foo', namespace='tenant-a')
        data2 = _make_instance_data(
            inst_uuid=_INSTANCE_UUID_2, name='foo', namespace='tenant-a')
        mock_find_instances.return_value = [data1, data2]

        with self.assertRaises(exceptions.MultipleObjects) as ctx:
            Instance.from_db_by_ref('foo', namespace='tenant-a')

        msg = str(ctx.exception)
        self.assertIn('foo', msg)
        self.assertIn('tenant-a', msg)
